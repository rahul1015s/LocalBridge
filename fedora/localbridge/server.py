"""HTTPS receiver API (FastAPI) and the start/stop service wrapping uvicorn."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from . import PROTOCOL_VERSION, __version__
from .certificates import TlsIdentity, generate_identity
from .models import (
    ApiError,
    FileState,
    FinalizeRequest,
    Limits,
    PairRequest,
    PairResponse,
    TransferRequest,
    TransferState,
)
from .pairing import AuthSession, PairingManager
from .security import FailureLimiter
from .storage import PARTIAL_DIR, Destination, PartialFile
from .transfers import Transfer, TransferManager


@dataclass
class ReceiverConfig:
    host: str
    port: int
    destination: Path
    device_name: str = "Fedora"
    limits: Limits = field(default_factory=Limits)


class Core:
    """Shared state; independent of any UI."""

    def __init__(self, cfg: ReceiverConfig):
        self.cfg = cfg
        self.limits = cfg.limits
        self.pairing = PairingManager(cfg.limits)
        self.transfers = TransferManager(cfg.limits)
        self.dest = Destination(cfg.destination, cfg.limits.min_free_space_margin)
        self.pair_limiter = FailureLimiter(
            cfg.limits.pair_fail_limit, cfg.limits.pair_fail_window_seconds
        )
        self.partials: dict[tuple[str, str], PartialFile] = {}
        self._plock = threading.Lock()
        self.transfers.subscribe(self._cleanup_on_terminal)

    def _cleanup_on_terminal(self, t: Transfer) -> None:
        if t.state in (
            TransferState.CANCELLED,
            TransferState.EXPIRED,
            TransferState.REJECTED,
            TransferState.FAILED,
        ):
            self.discard_partials(t.transfer_id)

    def discard_partials(self, transfer_id: str | None = None) -> None:
        with self._plock:
            for key in [k for k in self.partials if transfer_id in (None, k[0])]:
                pf = self.partials.pop(key)
                # A stream may still be writing; cancel flags make it stop, and it re-checks.
                pf.discard()


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def _read_json(request: Request, model: type[BaseModel], max_bytes: int):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        raise ApiError(413, "body_too_large", "Request body too large.")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > max_bytes:
            raise ApiError(413, "body_too_large", "Request body too large.")
    try:
        return model.model_validate(json.loads(bytes(buf) or b"{}"))
    except (ValueError, ValidationError) as e:
        raise ApiError(422, "invalid_request", "Malformed request.") from e


def create_app(core: Core) -> FastAPI:
    # No docs/openapi/redoc: no unnecessary web endpoints.
    app = FastAPI(
        title="LocalBridge", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return _err(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, __: RequestValidationError):
        return _err(422, "invalid_request", "Malformed request.")  # never echo input back

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, __: Exception):
        return _err(500, "internal_error", "Internal error.")

    def auth(request: Request) -> AuthSession:
        h = request.headers.get("authorization", "")
        if not h.lower().startswith("bearer ") or len(h) > 256:
            raise ApiError(401, "unauthorized", "Missing, expired or revoked session.")
        return core.pairing.authenticate(h[7:].strip())

    # -- health: deliberately minimal ----------------------------------------------
    @app.get("/v1/health")
    async def health():
        return {"status": "ok", "protocol": PROTOCOL_VERSION}

    # -- pairing --------------------------------------------------------------------
    @app.post("/v1/pair", response_model=PairResponse)
    async def pair(request: Request):
        ip = request.client.host if request.client else "?"
        if core.pair_limiter.blocked(ip):
            raise ApiError(429, "rate_limited", "Too many failed attempts. Try again later.")
        req = await _read_json(request, PairRequest, core.limits.max_json_body)
        try:
            pending = core.pairing.begin(req.session_id, req.secret, req.device_name, ip)
        except ApiError as e:
            if e.status == 401:
                core.pair_limiter.record_failure(ip)
            raise
        # Wait for the local user's explicit decision (polled; loop-agnostic).
        deadline = time.monotonic() + core.limits.pairing_confirm_timeout
        while pending.decision is None and time.monotonic() < deadline:
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.2)
        result = core.pairing.finish(pending, timed_out=pending.decision is None)
        if result is None:
            raise ApiError(403, "pairing_denied", "Pairing was not confirmed on the receiver.")
        session, token = result
        core.pair_limiter.reset(ip)
        return PairResponse(
            token=token,
            expires_in=core.limits.session_ttl_seconds,
            receiver_name=core.cfg.device_name,
        )

    # -- transfers ---------------------------------------------------------------------
    def view(t: Transfer) -> dict:
        return {
            "transfer_id": t.transfer_id,
            "state": t.state.value,
            "reason": t.reason,
            "files": [
                {
                    "file_id": f.file_id,
                    "name": f.name,
                    "size": f.declared_size,
                    "state": f.state.value,
                    "received": f.received,
                    "sha256": f.sha256,
                    "error": f.error,
                }
                for f in t.files
            ],
        }

    @app.post("/v1/transfers", status_code=201)
    async def create_transfer(request: Request, s: AuthSession = Depends(auth)):
        req = await _read_json(request, TransferRequest, core.limits.max_json_body)
        t = core.transfers.create(s.session_id, s.device_name, req.files)
        return view(t)

    @app.get("/v1/transfers/{transfer_id}")
    async def get_transfer(transfer_id: str, s: AuthSession = Depends(auth)):
        return view(core.transfers.get(transfer_id, s.session_id))

    @app.post("/v1/transfers/{transfer_id}/cancel")
    async def cancel_transfer(transfer_id: str, s: AuthSession = Depends(auth)):
        return view(core.transfers.cancel(transfer_id, s.session_id, "Cancelled by sender."))

    @app.put("/v1/transfers/{transfer_id}/files/{file_id}/content")
    async def put_content(
        transfer_id: str, file_id: str, request: Request, s: AuthSession = Depends(auth)
    ):
        mgr, lim = core.transfers, core.limits
        t, f = mgr.begin_upload(transfer_id, s.session_id, file_id)
        partial: PartialFile | None = None
        try:
            core.dest.validate()
            expected = f.declared_size
            cl = request.headers.get("content-length")
            if cl is not None:
                if not cl.isdigit():
                    raise ApiError(400, "bad_length", "Invalid Content-Length.")
                if expected is not None and int(cl) != expected:
                    raise ApiError(400, "size_mismatch", "Body size differs from declared size.")
                expected = int(cl)
            if expected is not None:
                if expected > lim.max_file_size:
                    raise ApiError(413, "file_too_large", "File exceeds the size limit.")
                core.dest.check_space(expected)
            partial = core.dest.open_partial(transfer_id, file_id)
            with core._plock:
                core.partials[(transfer_id, file_id)] = partial
            buf = bytearray()
            total = 0
            async for chunk in request.stream():
                if f.state != FileState.RECEIVING or not mgr.is_live(t):
                    raise ApiError(409, "cancelled", "Transfer is no longer active.")
                total += len(chunk)
                if total > lim.max_file_size or (expected is not None and total > expected):
                    raise ApiError(413, "file_too_large", "Body larger than allowed.")
                buf += chunk
                if len(buf) >= lim.write_chunk:
                    data = bytes(buf)
                    buf.clear()
                    await asyncio.to_thread(partial.write, data)
                    mgr.progress(t, f, partial.written)
            if buf:
                await asyncio.to_thread(partial.write, bytes(buf))
            if f.state != FileState.RECEIVING or not mgr.is_live(t):
                raise ApiError(409, "cancelled", "Transfer is no longer active.")
            if expected is not None and partial.written != expected:
                raise ApiError(400, "size_mismatch", "Body shorter than declared size.")
            mgr.progress(t, f, partial.written)
            mgr.mark_verifying(t, f)
            return {"received": partial.written, "sha256": partial.hexdigest()}
        except BaseException as e:
            if partial is not None:
                with core._plock:
                    core.partials.pop((transfer_id, file_id), None)
                partial.discard()
            msg = e.message if isinstance(e, ApiError) else "Upload failed."
            mgr.finish_file(t, f, ok=False, error=msg)
            raise
        finally:
            mgr.upload_ended(t, f)

    @app.post("/v1/transfers/{transfer_id}/files/{file_id}/finalize")
    async def finalize(
        transfer_id: str, file_id: str, request: Request, s: AuthSession = Depends(auth)
    ):
        req = await _read_json(request, FinalizeRequest, core.limits.max_json_body)
        mgr = core.transfers
        t = mgr.get(transfer_id, s.session_id)
        f = next((x for x in t.files if x.file_id == file_id), None)
        if f is None:
            raise ApiError(404, "not_found", "File not found.")
        with core._plock:
            partial = core.partials.pop((transfer_id, file_id), None)
        if partial is None or f.state != FileState.VERIFYING:
            raise ApiError(409, "file_state", "File is not awaiting verification.")
        partial.close()
        actual = partial.hexdigest()
        if actual != req.sha256 or partial.written != req.size:
            partial.discard()
            mgr.finish_file(t, f, ok=False, sha256=actual, error="Integrity check failed.")
            raise ApiError(422, "integrity_failed", "SHA-256 or size does not match.")
        try:
            path = await asyncio.to_thread(core.dest.finalize, partial.path, f.name)
        except ApiError as e:
            partial.discard()
            mgr.finish_file(t, f, ok=False, error=e.message)
            raise
        mgr.finish_file(t, f, ok=True, path=path, sha256=actual)
        return view(t)

    return app


class ReceiverService:
    """Owns the TLS identity and the uvicorn server; fully stoppable."""

    def __init__(self, cfg: ReceiverConfig):
        self.core = Core(cfg)
        self.identity: TlsIdentity | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 10.0) -> None:
        if self.running:
            return
        cfg = self.core.cfg
        self.core.dest.validate()
        purge_partials(cfg.destination)
        self.identity = generate_identity(cfg.host)
        config = uvicorn.Config(
            create_app(self.core),
            host=cfg.host,
            port=cfg.port,
            ssl_certfile=str(self.identity.cert_path),
            ssl_keyfile=str(self.identity.key_path),
            log_level="warning",
            access_log=False,
            server_header=False,
            date_header=False,
            timeout_keep_alive=5,
            limit_concurrency=64,
            ws="none",
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # not in main thread
        self._thread = threading.Thread(
            target=self._server.run, name="localbridge-server", daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("Receiver failed to start (is the port already in use?).")
            time.sleep(0.02)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    def pairing_uri(self) -> str:
        """URI for the QR code; only valid while an unexpired offer exists."""
        offer = self.core.pairing.current_offer() or self.core.pairing.new_offer()
        assert self.identity is not None  # noqa: S101
        q = urlencode(
            {
                "v": PROTOCOL_VERSION,
                "host": self.core.cfg.host,
                "port": self.port,
                "sid": offer.session_id,
                "fp": self.identity.fingerprint,
                "secret": offer.secret,
            }
        )
        return f"localbridge://pair?{q}"

    def stop(self, timeout: float = 10.0) -> None:
        self.core.transfers.cancel_all("Receiver stopped.")
        self.core.pairing.revoke_all()
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout)
        self.core.discard_partials()
        purge_partials(self.core.cfg.destination)
        if self.identity:
            self.identity.cleanup()
            self.identity = None
        self._server = self._thread = None


def purge_partials(destination: Path) -> None:
    d = destination / PARTIAL_DIR
    if d.is_dir() and not d.is_symlink():
        shutil.rmtree(d, ignore_errors=True)

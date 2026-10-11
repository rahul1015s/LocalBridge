"""Transfer lifecycle: validated state machine, approval binding and limits.

Approval is a *local* action (``approve``/``reject`` are called by the desktop
UI, never exposed over the network) so a sender can never approve itself.
All mutation is guarded by a lock because the UI and the server loop run on
different threads.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    TERMINAL_STATES,
    ApiError,
    FileSpec,
    FileState,
    Limits,
    TransferState,
)
from .security import new_id
from .storage import sanitize_filename, sanitize_relpath

log = logging.getLogger(__name__)

S = TransferState
_ALLOWED: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.AWAITING_APPROVAL, S.CANCELLED, S.FAILED}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.CANCELLED, S.EXPIRED}),
    S.APPROVED: frozenset({S.TRANSFERRING, S.CANCELLED, S.EXPIRED, S.FAILED}),
    S.TRANSFERRING: frozenset({S.VERIFYING, S.COMPLETED, S.FAILED, S.CANCELLED}),
    S.VERIFYING: frozenset({S.TRANSFERRING, S.COMPLETED, S.FAILED, S.CANCELLED}),
    S.COMPLETED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
    S.FAILED: frozenset(),
    S.EXPIRED: frozenset(),
}


@dataclass
class FileEntry:
    file_id: str
    name: str  # sanitised display / final name
    declared_size: int | None
    state: FileState = FileState.PENDING
    received: int = 0
    sha256: str | None = None
    saved_path: Path | None = None
    error: str | None = None
    dirs: list[str] = field(default_factory=list)  # validated relative folder components
    epoch: int = 0  # bumps when a new stream supersedes a silent one
    last_activity: float = 0.0


@dataclass
class Transfer:
    transfer_id: str
    session_id: str
    device_name: str
    files: list[FileEntry]
    created_at: float
    state: TransferState = S.CREATED
    updated_at: float = 0.0
    reason: str | None = None
    uploads_in_flight: int = 0
    top_map: dict[str, str] = field(default_factory=dict)  # requested -> actual top-level folder

    @property
    def total_declared(self) -> int:
        return sum(f.declared_size or 0 for f in self.files)

    @property
    def total_received(self) -> int:
        return sum(f.received for f in self.files)

    @property
    def size_known(self) -> bool:
        return all(f.declared_size is not None for f in self.files)


class TransferManager:
    def __init__(self, limits: Limits, clock=time.monotonic):
        self._limits = limits
        self._clock = clock
        self._lock = threading.RLock()
        self._transfers: dict[str, Transfer] = {}
        self._listeners: list = []

    # -- events -----------------------------------------------------------------
    def subscribe(self, cb) -> None:
        self._listeners.append(cb)

    def _emit(self, t: Transfer) -> None:
        for cb in list(self._listeners):
            try:
                cb(t)
            except Exception:  # noqa: BLE001 - UI listeners must never break the protocol
                log.exception("listener failed")

    # -- core transitions --------------------------------------------------------
    def _move(self, t: Transfer, new: S, reason: str | None = None) -> None:
        if t.state == new:
            return
        if new not in _ALLOWED[t.state]:
            raise ApiError(409, "invalid_state", f"Cannot go from {t.state} to {new}.")
        t.state = new
        t.updated_at = self._clock()
        if reason:
            t.reason = reason

    def _get(self, transfer_id: str, session_id: str | None) -> Transfer:
        t = self._transfers.get(transfer_id)
        # Same error whether missing or owned by someone else: no enumeration.
        if t is None or (session_id is not None and t.session_id != session_id):
            raise ApiError(404, "not_found", "Transfer not found.")
        self._expire(t)
        return t

    def _expire(self, t: Transfer) -> bool:
        now = self._clock()
        if (
            t.state in (S.AWAITING_APPROVAL, S.APPROVED)
            and now - t.updated_at > self._limits.approval_timeout_seconds
        ):
            self._move(t, S.EXPIRED, "Approval timed out.")
            return True
        if (
            t.state == S.TRANSFERRING
            and t.uploads_in_flight == 0
            and now - t.updated_at > self._limits.resume_window_seconds
        ):
            for f in t.files:
                if f.state not in (FileState.DONE, FileState.FAILED, FileState.CANCELLED):
                    f.state, f.error = FileState.FAILED, "Resume window expired."
            self._move(t, S.FAILED, "Interrupted transfer was not resumed in time.")
            return True
        return False

    # -- creation -----------------------------------------------------------------
    def create(self, session_id: str, device_name: str, specs: list[FileSpec]) -> Transfer:
        lim = self._limits
        if len(specs) > lim.max_files_per_transfer:
            raise ApiError(413, "too_many_files", "Too many files in one transfer.")
        total = 0
        entries = []
        for spec in specs:
            if spec.size is not None:
                if spec.size > lim.max_file_size:
                    raise ApiError(413, "file_too_large", "A file exceeds the size limit.")
                total += spec.size
            name = sanitize_filename(spec.name, lim.max_filename_bytes)
            dirs = sanitize_relpath(spec.dir, lim.max_dir_depth, lim.max_filename_bytes)
            entries.append(FileEntry(new_id(), name, spec.size, dirs=dirs))
        if total > lim.max_transfer_size:
            raise ApiError(413, "transfer_too_large", "Transfer exceeds the aggregate limit.")
        with self._lock:
            pending = sum(
                1
                for x in self._transfers.values()
                if x.session_id == session_id and x.state in (S.AWAITING_APPROVAL, S.APPROVED)
            )
            if pending >= lim.max_pending_transfers:
                raise ApiError(429, "too_many_pending", "Too many transfers awaiting approval.")
            now = self._clock()
            t = Transfer(new_id(), session_id, device_name, entries, now, updated_at=now)
            self._transfers[t.transfer_id] = t
            self._move(t, S.AWAITING_APPROVAL)
        self._emit(t)
        return t

    # -- local (UI) decisions --------------------------------------------------------
    def approve(self, transfer_id: str) -> None:
        with self._lock:
            t = self._get(transfer_id, None)
            self._move(t, S.APPROVED)
        self._emit(t)

    def reject(self, transfer_id: str) -> None:
        with self._lock:
            t = self._get(transfer_id, None)
            self._move(t, S.REJECTED, "Rejected by receiver.")
        self._emit(t)

    # -- sender operations -----------------------------------------------------------
    def get(self, transfer_id: str, session_id: str) -> Transfer:
        with self._lock:
            before = None
            t = self._get(transfer_id, session_id)
            before = t.state
        if before in TERMINAL_STATES and before in (S.EXPIRED, S.FAILED):
            self._emit(t)  # lets listeners clean up partial files after an expiry
        return t

    def cancel(
        self, transfer_id: str, session_id: str | None, reason: str = "Cancelled."
    ) -> Transfer:
        with self._lock:
            t = self._get(transfer_id, session_id)
            if t.state in TERMINAL_STATES:
                return t  # idempotent
            self._move(t, S.CANCELLED, reason)
            for f in t.files:
                if f.state in (FileState.PENDING, FileState.RECEIVING, FileState.VERIFYING):
                    f.state = FileState.CANCELLED
        self._emit(t)
        return t

    def begin_upload(
        self, transfer_id: str, session_id: str, file_id: str
    ) -> tuple[Transfer, FileEntry, int]:
        """Reserve a content stream. Content is refused until the user approved.

        Returns the epoch of this stream; a newer stream supersedes an older silent one.
        """
        with self._lock:
            t = self._get(transfer_id, session_id)
            if t.state not in (S.APPROVED, S.TRANSFERRING, S.VERIFYING):
                code = "not_approved" if t.state == S.AWAITING_APPROVAL else "invalid_state"
                raise ApiError(409, code, f"Transfer is {t.state}; content not accepted.")
            f = next((x for x in t.files if x.file_id == file_id), None)
            if f is None:
                raise ApiError(404, "not_found", "File not found.")
            now = self._clock()
            if f.state == FileState.RECEIVING:
                if now - f.last_activity < self._limits.takeover_idle_seconds:
                    raise ApiError(409, "file_state", "File is already being uploaded.")
                f.epoch += 1  # the previous stream went silent: supersede it
            elif f.state not in (FileState.PENDING, FileState.INTERRUPTED):
                raise ApiError(409, "file_state", f"File is {f.state}; cannot upload again.")
            elif f.state in (FileState.PENDING, FileState.INTERRUPTED):
                active = sum(1 for x in self._transfers.values() if x.uploads_in_flight > 0)
                if t.uploads_in_flight == 0 and active >= self._limits.max_active_transfers:
                    raise ApiError(429, "too_many_active", "Too many active transfers.")
                total_up = sum(x.uploads_in_flight for x in self._transfers.values())
                if total_up >= self._limits.max_concurrent_uploads:
                    raise ApiError(429, "too_many_uploads", "Too many simultaneous uploads.")
            f.state = FileState.RECEIVING
            f.last_activity = now
            t.uploads_in_flight += 1
            t.updated_at = now
            if t.state == S.APPROVED:
                self._move(t, S.TRANSFERRING)
            epoch = f.epoch
        self._emit(t)
        return t, f, epoch

    def progress(self, t: Transfer, f: FileEntry, received: int) -> None:
        with self._lock:
            f.received = received
            f.last_activity = t.updated_at = self._clock()
        self._emit(t)

    def is_live(self, t: Transfer) -> bool:
        return t.state in (S.TRANSFERRING, S.VERIFYING)

    def mark_verifying(self, t: Transfer, f: FileEntry) -> None:
        with self._lock:
            f.state = FileState.VERIFYING
        self._emit(t)

    def finish_file(
        self,
        t: Transfer,
        f: FileEntry,
        *,
        ok: bool,
        path: Path | None = None,
        sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if f.state != FileState.CANCELLED:
                f.state = FileState.DONE if ok else FileState.FAILED
            f.saved_path, f.sha256, f.error = path, sha256, error
            self._settle(t)
        self._emit(t)

    def upload_ended(self, t: Transfer, f: FileEntry, epoch: int) -> None:
        """Always called when a content stream ends; a dropped stream becomes INTERRUPTED."""
        with self._lock:
            t.uploads_in_flight = max(0, t.uploads_in_flight - 1)
            t.updated_at = self._clock()
            if f.epoch == epoch and f.state == FileState.RECEIVING:
                f.state = FileState.INTERRUPTED  # resumable; the partial file is kept
                f.error = "Connection interrupted."
            self._settle(t)
        self._emit(t)

    def _settle(self, t: Transfer) -> None:
        if t.state in TERMINAL_STATES:
            return
        if all(f.state in (FileState.DONE, FileState.FAILED, FileState.CANCELLED) for f in t.files):
            if all(f.state == FileState.DONE for f in t.files):
                self._move(t, S.COMPLETED)
            else:
                self._move(t, S.FAILED, "One or more files did not complete.")

    # -- housekeeping ------------------------------------------------------------------
    def all(self) -> list[Transfer]:
        with self._lock:
            changed = [t for t in self._transfers.values() if self._expire(t)]
            out = list(self._transfers.values())
        for t in changed:
            self._emit(t)
        return out

    def cancel_all(self, reason: str) -> None:
        for t in self.all():
            if t.state not in TERMINAL_STATES:
                self.cancel(t.transfer_id, None, reason)

    def clear_finished(self) -> None:
        with self._lock:
            for k in [k for k, t in self._transfers.items() if t.state in TERMINAL_STATES]:
                del self._transfers[k]

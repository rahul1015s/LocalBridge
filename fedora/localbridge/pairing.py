"""Short-lived single-use pairing and authenticated sessions."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from .models import ApiError, Limits
from .security import constant_time_equal, new_id, new_token, token_digest

log = logging.getLogger(__name__)


@dataclass
class PairingOffer:
    """What the QR code advertises. The secret is single use and expires fast."""

    session_id: str
    secret: str
    expires_at: float
    redeemed: bool = False
    revoked: bool = False


@dataclass
class PendingPairing:
    pairing_id: str
    device_name: str
    client_ip: str
    decision: bool | None = None  # None = waiting for the local user


@dataclass
class AuthSession:
    session_id: str
    device_name: str
    token_hash: str
    expires_at: float
    revoked: bool = False
    transfers: set[str] = field(default_factory=set)


class PairingManager:
    def __init__(self, limits: Limits, clock=time.monotonic):
        self._limits = limits
        self._clock = clock
        self._lock = threading.RLock()
        self._offer: PairingOffer | None = None
        self._pending: dict[str, PendingPairing] = {}
        self._sessions: dict[str, AuthSession] = {}  # keyed by token digest
        self._listeners: list = []

    def subscribe(self, cb) -> None:
        self._listeners.append(cb)

    def _emit(self, event: str, payload) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, payload)
            except Exception:  # noqa: BLE001 - UI listeners must never break the protocol
                log.exception("listener failed")

    # -- offers -----------------------------------------------------------------
    def new_offer(self) -> PairingOffer:
        """Create a fresh offer, invalidating any previous one."""
        with self._lock:
            if self._offer:
                self._offer.revoked = True
            self._offer = PairingOffer(
                session_id=new_id(),
                secret=new_token(32),
                expires_at=self._clock() + self._limits.pairing_ttl_seconds,
            )
            return self._offer

    def revoke_offer(self) -> None:
        with self._lock:
            if self._offer:
                self._offer.revoked = True
                self._offer = None

    def current_offer(self) -> PairingOffer | None:
        with self._lock:
            o = self._offer
            if o and (o.revoked or o.redeemed or self._clock() >= o.expires_at):
                return None
            return o

    # -- redemption ---------------------------------------------------------------
    def begin(
        self, session_id: str, secret: str, device_name: str, client_ip: str
    ) -> PendingPairing:
        """Validate and consume the offer; return a pending pairing for user confirmation.

        All credential failures return the same error to avoid an oracle.
        """
        with self._lock:
            offer = self._offer
            ok = (
                offer is not None
                and not offer.revoked
                and not offer.redeemed
                and self._clock() < offer.expires_at
            )
            # Always compare both values to keep timing uniform.
            sid_ok = constant_time_equal(session_id, offer.session_id if offer else "-")
            sec_ok = constant_time_equal(secret, offer.secret if offer else "-")
            if not (ok and sid_ok and sec_ok):
                raise ApiError(401, "pairing_invalid", "Pairing code is invalid or expired.")
            if len(self._pending) >= self._limits.max_pending_pairings:
                raise ApiError(429, "pairing_busy", "Too many pairing attempts in progress.")
            offer.redeemed = True  # single use: replay is impossible from here on
            p = PendingPairing(new_id(), device_name, client_ip)
            self._pending[p.pairing_id] = p
        self._emit("pairing_requested", p)
        return p

    def decide(self, pairing_id: str, accept: bool) -> None:
        """Called by the local UI."""
        with self._lock:
            p = self._pending.get(pairing_id)
            if p and p.decision is None:
                p.decision = accept

    def finish(self, p: PendingPairing, timed_out: bool = False) -> tuple[AuthSession, str] | None:
        """Resolve a pending pairing into a session (if accepted)."""
        with self._lock:
            self._pending.pop(p.pairing_id, None)
            if timed_out or not p.decision:
                return None
            self._prune_sessions()
            if len(self._sessions) >= self._limits.max_sessions:
                raise ApiError(429, "too_many_sessions", "Too many paired devices.")
            token = new_token(32)
            s = AuthSession(
                session_id=new_id(),
                device_name=p.device_name,
                token_hash=token_digest(token),
                expires_at=self._clock() + self._limits.session_ttl_seconds,
            )
            self._sessions[s.token_hash] = s
        self._emit("session_created", s)
        return s, token  # the raw token is handed out exactly once

    def pending(self) -> list[PendingPairing]:
        with self._lock:
            return list(self._pending.values())

    # -- sessions -------------------------------------------------------------------
    def _prune_sessions(self) -> None:
        now = self._clock()
        for k in [k for k, s in self._sessions.items() if s.revoked or now >= s.expires_at]:
            del self._sessions[k]

    def authenticate(self, token: str) -> AuthSession:
        with self._lock:
            s = self._sessions.get(token_digest(token))
            if not s or s.revoked or self._clock() >= s.expires_at:
                raise ApiError(401, "unauthorized", "Missing, expired or revoked session.")
            return s

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            for s in self._sessions.values():
                if s.session_id == session_id:
                    s.revoked = True
        self._emit("session_revoked", session_id)

    def revoke_all(self) -> None:
        with self._lock:
            for s in self._sessions.values():
                s.revoked = True
            self._offer = None
            for p in self._pending.values():
                p.decision = False

    def sessions(self) -> list[AuthSession]:
        with self._lock:
            self._prune_sessions()
            return list(self._sessions.values())

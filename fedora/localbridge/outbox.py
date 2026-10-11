"""Computer -> phone: files and text the desktop user chose to send.

Nothing here is reachable without a session, and a session can only fetch items addressed to it.
The phone user must still accept each item on the phone. The remote side only ever supplies an
item id; file paths are chosen locally and never leave this process.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import ApiError, Limits
from .security import new_id

log = logging.getLogger(__name__)

PREPARING, READY, DELIVERED, DECLINED, FAILED = (
    "PREPARING",
    "READY",
    "DELIVERED",
    "DECLINED",
    "FAILED",
)


@dataclass
class OutboxItem:
    item_id: str
    session_id: str
    kind: str  # "file" | "text"
    name: str
    size: int = 0
    text: str | None = None
    path: Path | None = None
    sha256: str | None = None
    state: str = PREPARING
    detail: str | None = None
    created: float = field(default_factory=time.time)
    _stat: tuple[int, int] | None = None  # (size, mtime_ns) when hashed


class OutboxManager:
    def __init__(self, limits: Limits):
        self._limits = limits
        self._lock = threading.RLock()
        self._items: dict[str, OutboxItem] = {}
        self._listeners: list = []

    def subscribe(self, cb) -> None:
        self._listeners.append(cb)

    def _emit(self, item: OutboxItem) -> None:
        for cb in list(self._listeners):
            try:
                cb(item)
            except Exception:  # noqa: BLE001 - UI errors must not break the protocol
                log.exception("outbox listener failed")

    def _check_capacity(self) -> None:
        live = [i for i in self._items.values() if i.state in (PREPARING, READY)]
        if len(live) >= self._limits.max_outbox_items:
            raise ApiError(429, "outbox_full", "Too many items waiting to be sent.")

    def add_text(self, session_id: str, text: str) -> OutboxItem:
        if not text or len(text.encode()) > self._limits.max_message_bytes:
            raise ApiError(413, "message_too_large", "Message is empty or too large.")
        with self._lock:
            self._check_capacity()
            item = OutboxItem(
                new_id(), session_id, "text", "Text", len(text.encode()), text=text, state=READY
            )
            self._items[item.item_id] = item
        self._emit(item)
        return item

    def add_file(self, session_id: str, path: Path) -> OutboxItem:
        p = Path(path).resolve()
        if not p.is_file():
            raise ApiError(400, "not_a_file", "Only regular files can be sent.")
        st = p.stat()
        with self._lock:
            self._check_capacity()
            item = OutboxItem(new_id(), session_id, "file", p.name, st.st_size, path=p)
            self._items[item.item_id] = item
        self._emit(item)
        threading.Thread(target=self._hash, args=(item,), daemon=True, name="outbox-hash").start()
        return item

    def _hash(self, item: OutboxItem) -> None:
        try:
            assert item.path is not None  # noqa: S101
            st = item.path.stat()
            h = hashlib.sha256()
            with item.path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
            with self._lock:
                if item.state != PREPARING:
                    return
                item.sha256, item._stat = h.hexdigest(), (st.st_size, st.st_mtime_ns)
                item.size, item.state = st.st_size, READY
        except OSError as e:
            with self._lock:
                item.state, item.detail = FAILED, f"Could not read file: {e.strerror}"
        self._emit(item)

    # -- queries ---------------------------------------------------------------------
    def items(self) -> list[OutboxItem]:
        with self._lock:
            return sorted(self._items.values(), key=lambda i: i.created, reverse=True)

    def pending_for(self, session_id: str) -> list[OutboxItem]:
        with self._lock:
            return [
                i for i in self._items.values() if i.session_id == session_id and i.state == READY
            ]

    def get_for_download(self, item_id: str, session_id: str) -> OutboxItem:
        with self._lock:
            i = self._items.get(item_id)
            if i is None or i.session_id != session_id or i.kind != "file" or i.state != READY:
                raise ApiError(404, "not_found", "Item not found.")
            assert i.path is not None  # noqa: S101
            try:
                st = i.path.stat()
            except OSError:
                i.state, i.detail = FAILED, "File is no longer available."
                raise ApiError(410, "gone", "File is no longer available.") from None
            if (st.st_size, st.st_mtime_ns) != i._stat:
                i.state, i.detail = FAILED, "File changed after it was queued."
                raise ApiError(409, "file_changed", "File changed after it was queued.")
            return i

    def mark(self, item_id: str, session_id: str, status: str, detail: str | None) -> OutboxItem:
        with self._lock:
            i = self._items.get(item_id)
            if i is None or i.session_id != session_id:
                raise ApiError(404, "not_found", "Item not found.")
            i.state = {"saved": DELIVERED, "declined": DECLINED, "failed": FAILED}[status]
            i.detail = detail
        self._emit(i)
        return i

    def cancel(self, item_id: str) -> None:
        """Local user withdraws an item before it was fetched."""
        with self._lock:
            i = self._items.get(item_id)
            if i and i.state in (PREPARING, READY):
                i.state, i.detail = DECLINED, "Withdrawn on the computer."
        if i:
            self._emit(i)

    def clear_finished(self) -> None:
        with self._lock:
            for k in [
                k for k, i in self._items.items() if i.state in (DELIVERED, DECLINED, FAILED)
            ]:
                del self._items[k]

    def drop_session(self, session_id: str | None = None) -> None:
        with self._lock:
            for i in self._items.values():
                if i.state in (PREPARING, READY) and session_id in (None, i.session_id):
                    i.state, i.detail = DECLINED, "Phone disconnected."

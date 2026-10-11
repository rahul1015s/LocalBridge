"""Protocol models, limits and enums shared by the receiver components."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SHA256_HEX = r"^[0-9a-f]{64}$"
ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


@dataclass(frozen=True)
class Limits:
    """Configurable resource limits. Defaults are deliberately finite."""

    max_file_size: int = 64 * 1024**3  # 64 GiB per file
    max_transfer_size: int = 256 * 1024**3  # aggregate per transfer
    max_files_per_transfer: int = 1000
    max_filename_bytes: int = 255
    max_active_transfers: int = 2  # transfers receiving content at once
    max_pending_transfers: int = 8  # awaiting approval per session
    max_concurrent_uploads: int = 4  # simultaneous content streams overall
    max_sessions: int = 4
    max_pending_pairings: int = 2
    pairing_ttl_seconds: int = 120
    pairing_confirm_timeout: int = 60
    session_ttl_seconds: int = 3600
    approval_timeout_seconds: int = 180
    pair_fail_limit: int = 5
    pair_fail_window_seconds: int = 60
    max_json_body: int = 1024 * 1024  # 1 MiB metadata
    min_free_space_margin: int = 64 * 1024**2  # keep 64 MiB free after write
    write_chunk: int = 1024 * 1024
    resume_window_seconds: int = 900  # how long an interrupted upload may be resumed
    takeover_idle_seconds: int = 8  # a silent stream older than this may be superseded
    max_message_bytes: int = 64 * 1024
    max_messages: int = 100
    max_outbox_items: int = 50


class TransferState(enum.StrEnum):
    CREATED = "CREATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    TRANSFERRING = "TRANSFERRING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


TERMINAL_STATES = frozenset(
    {
        TransferState.COMPLETED,
        TransferState.REJECTED,
        TransferState.CANCELLED,
        TransferState.FAILED,
        TransferState.EXPIRED,
    }
)


class FileState(enum.StrEnum):
    PENDING = "PENDING"
    RECEIVING = "RECEIVING"
    VERIFYING = "VERIFYING"
    INTERRUPTED = "INTERRUPTED"  # connection dropped; resumable from `received`
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairRequest(_Strict):
    protocol: int = Field(ge=1, le=1)
    session_id: str = Field(pattern=ID_PATTERN)
    secret: str = Field(min_length=16, max_length=128)
    device_name: str = Field(min_length=1, max_length=64)


class PairResponse(_Strict):
    token: str
    expires_in: int
    receiver_name: str


class FileSpec(_Strict):
    name: str = Field(min_length=1, max_length=1024)
    size: int | None = Field(default=None, ge=0)
    mime: str | None = Field(default=None, max_length=127)


class TransferRequest(_Strict):
    files: list[FileSpec] = Field(min_length=1)


class FinalizeRequest(_Strict):
    sha256: str = Field(pattern=SHA256_HEX)
    size: int = Field(ge=0)


class MessageRequest(_Strict):
    text: str = Field(min_length=1, max_length=65536)


class OutboxDone(_Strict):
    status: Literal["saved", "declined", "failed"]
    detail: str | None = Field(default=None, max_length=200)


class ApiError(Exception):
    """Controlled error carrying an HTTP status and a stable machine code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

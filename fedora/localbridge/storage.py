"""Safe, atomic, never-overwriting file storage inside one destination folder."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import unicodedata
from pathlib import Path

from .models import ApiError

PARTIAL_DIR = ".localbridge-partial"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RESERVED = {"", ".", ".."}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BIDI = re.compile("[‪-‮⁦-⁩‎‏]")


def sanitize_filename(raw: str, max_bytes: int = 255) -> str:
    """Reduce a remote-supplied name to a single, safe path component.

    Directory components are dropped (both separators), control and bidi
    override characters removed, Unicode normalised to NFC, and the result
    truncated on a character boundary (keeping the extension where possible).
    """
    name = unicodedata.normalize("NFC", raw)
    name = name.replace("\\", "/").split("/")[-1]
    name = _CONTROL.sub("", name)
    name = _BIDI.sub("", name)
    name = name.strip().strip(".").strip()
    if name in _RESERVED:
        name = "file"
    if len(name.encode()) > max_bytes:
        stem, dot, ext = name.rpartition(".")
        if not dot or len(ext.encode()) > 32:
            stem, ext = name, ""
        else:
            ext = "." + ext
        budget = max_bytes - len(ext.encode())
        while len(stem.encode()) > budget:
            stem = stem[:-1]
        name = (stem or "file") + ext
    return name


def candidate_names(name: str, max_bytes: int = 255):
    yield name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    else:
        ext = "." + ext
    for i in range(1, 10_000):
        suffix = f" ({i})"
        s = stem
        while len((s + suffix + ext).encode()) > max_bytes:
            s = s[:-1]
        yield f"{s}{suffix}{ext}"


class Destination:
    """A user-chosen directory into which received files are finalised."""

    def __init__(self, path: Path, margin: int = 0):
        self.path = Path(path)
        self.margin = margin

    def validate(self) -> None:
        p = self.path
        if not p.is_dir():
            raise ApiError(507, "destination_missing", "Destination folder does not exist.")
        if not os.access(p, os.W_OK | os.X_OK):
            raise ApiError(507, "destination_unwritable", "Destination folder is not writable.")

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.path).free

    def check_space(self, needed: int) -> None:
        if needed + self.margin > self.free_bytes():
            raise ApiError(507, "insufficient_storage", "Not enough free disk space.")

    def _partial_dir(self) -> Path:
        d = self.path / PARTIAL_DIR
        d.mkdir(mode=0o700, exist_ok=True)
        if d.is_symlink() or not d.is_dir():
            raise ApiError(500, "storage_error", "Unsafe partial directory.")
        return d

    def open_partial(self, transfer_id: str, file_id: str) -> PartialFile:
        self.validate()
        # IDs are server-generated URL-safe tokens; still refuse anything odd.
        if not (_ID.fullmatch(transfer_id) and _ID.fullmatch(file_id)):
            raise ApiError(400, "bad_id", "Invalid identifier.")
        path = self._partial_dir() / f"{transfer_id}.{file_id}.part"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        return PartialFile(self, path, fd)

    def finalize(self, partial_path: Path, name: str) -> Path:
        """Atomically move into place without ever overwriting an existing file."""
        safe = sanitize_filename(name)
        for cand in candidate_names(safe):
            target = self.path / cand
            try:
                os.link(partial_path, target)  # fails if target exists (no overwrite)
            except FileExistsError:
                continue
            except OSError as e:
                if e.errno in (errno.EPERM, errno.EXDEV, errno.ENOTSUP):
                    raise ApiError(500, "finalize_failed", "Could not finalise file.") from e
                raise
            os.unlink(partial_path)
            os.chmod(target, 0o644 & ~_umask())
            return target
        raise ApiError(409, "name_exhausted", "Could not find a free file name.")


def _umask() -> int:
    m = os.umask(0)
    os.umask(m)
    return m


class PartialFile:
    """Write handle that hashes the bytes actually written."""

    def __init__(self, dest: Destination, path: Path, fd: int):
        self.dest = dest
        self.path = path
        self._fd = fd
        self._sha = hashlib.sha256()
        self.written = 0
        self._closed = False

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        try:
            while view:
                n = os.write(self._fd, view)  # handles partial writes
                view = view[n:]
        except OSError as e:
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                raise ApiError(507, "disk_full", "Disk is full.") from e
            raise
        self._sha.update(data)
        self.written += len(data)

    def hexdigest(self) -> str:
        return self._sha.hexdigest()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._fd)

    def discard(self) -> None:
        self.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

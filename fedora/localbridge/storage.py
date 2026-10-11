"""Safe, atomic, never-overwriting file storage inside one destination folder."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path

from .models import ApiError

PARTIAL_DIR = ".localbridge-partial"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RESERVED = {"", ".", ".."}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BIDI = re.compile("[‪-‮⁦-⁩‎‏]")


_WIN_BAD = re.compile(r'[<>:"|?*]')
_WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _windows_safe(name: str) -> str:
    name = _WIN_BAD.sub("_", name).rstrip(" .") or "file"
    if name.split(".")[0].lower() in _WIN_RESERVED:
        name = "_" + name
    return name


def sanitize_filename(raw: str, max_bytes: int = 255, windows: bool | None = None) -> str:
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
    if os.name == "nt" if windows is None else windows:
        name = _windows_safe(name)
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


def sanitize_relpath(raw: str | None, max_depth: int = 16, max_bytes: int = 255) -> list[str]:
    """Validate a remote-supplied relative folder and return its safe components.

    Strict by design: anything that could climb out (``..``), start at a root (leading ``/`` or a
    drive), contain empty/``.`` components, control characters or NULs is *rejected*, not repaired,
    so a hostile path can never be silently turned into a different, valid one.
    """
    if raw is None or raw == "":
        return []
    bad = ApiError(422, "invalid_path", "Invalid folder path.")
    if "\x00" in raw or _CONTROL.search(raw) or _BIDI.search(raw):
        raise bad
    text = raw.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise bad
    parts = text.rstrip("/").split("/")
    if len(parts) > max_depth:
        raise bad
    out = []
    for p in parts:
        if p in ("", ".", "..") or p != p.strip() or p.endswith("."):
            raise bad
        out.append(sanitize_filename(p, max_bytes))
    return out


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


_HAS_DIR_FD = (
    os.link in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.open in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


class Destination:
    """A user-chosen directory into which received files are finalised."""

    def __init__(self, path: Path, margin: int = 0):
        self.path = Path(path)
        self.margin = margin
        self._top_lock = threading.Lock()

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

    def finalize(
        self,
        partial_path: Path,
        name: str,
        dirs: list[str] | None = None,
        top_map: dict[str, str] | None = None,
    ) -> Path:
        """Atomically move into place without ever overwriting an existing file.

        ``dirs`` are already-validated folder components. The first component (the transfer's
        top-level folder) is created with a collision alias (``Trip (1)``) recorded in ``top_map`` so
        every file of one transfer lands in the same new folder and nothing existing is merged into.
        """
        safe = sanitize_filename(name)
        comps = list(dirs or [])
        if _HAS_DIR_FD:
            return self._finalize_fd(partial_path, safe, comps, top_map)
        return self._finalize_path(partial_path, safe, comps, top_map)

    # POSIX: walk with directory file descriptors and O_NOFOLLOW so a symlink planted anywhere in
    # the destination can never redirect a write outside it (no check/use race).
    def _finalize_fd(self, partial_path, safe, comps, top_map) -> Path:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        opened = [os.open(self.path, flags)]
        rel = Path()
        try:
            for i, c in enumerate(comps):
                cur = opened[-1]
                if i == 0 and top_map is not None:
                    with self._top_lock:
                        used = top_map.get(c)
                        if used is None:
                            used = self._mkdir_unique_fd(cur, c)
                            top_map[c] = used
                    c = used
                else:
                    try:
                        os.mkdir(c, 0o755, dir_fd=cur)
                    except FileExistsError:
                        pass
                try:
                    opened.append(os.open(c, flags | os.O_NOFOLLOW, dir_fd=cur))
                except OSError as e:  # ELOOP/ENOTDIR: a symlink or file is in the way
                    raise ApiError(
                        409, "unsafe_path", "A folder in the destination is unsafe."
                    ) from e
                rel /= c
            for cand in candidate_names(safe):
                try:
                    os.link(partial_path, cand, dst_dir_fd=opened[-1], follow_symlinks=False)
                except FileExistsError:
                    continue
                except OSError as e:
                    raise ApiError(500, "finalize_failed", "Could not finalise file.") from e
                os.unlink(partial_path)
                os.chmod(cand, 0o644 & ~_umask(), dir_fd=opened[-1])
                return self.path / rel / cand
            raise ApiError(409, "name_exhausted", "Could not find a free file name.")
        finally:
            for fd in opened:
                os.close(fd)

    def _mkdir_unique_fd(self, fd: int, base: str) -> str:
        for cand in candidate_names(base):
            try:
                os.mkdir(cand, 0o755, dir_fd=fd)
                return cand
            except FileExistsError:
                continue
        raise ApiError(409, "name_exhausted", "Could not find a free folder name.")

    # Fallback for platforms without dir_fd support (Windows). Checks for links before use; this is
    # weaker against a local attacker racing the check, which the threat model documents.
    def _finalize_path(self, partial_path, safe, comps, top_map) -> Path:
        root = self.path.resolve()
        cur = root
        for i, c in enumerate(comps):
            if i == 0 and top_map is not None:
                with self._top_lock:
                    used = top_map.get(c)
                    if used is None:
                        for cand in candidate_names(c):
                            try:
                                (cur / cand).mkdir()
                                used = cand
                                break
                            except FileExistsError:
                                continue
                        if used is None:
                            raise ApiError(
                                409, "name_exhausted", "Could not find a free folder name."
                            )
                        top_map[c] = used
                c = used
            nxt = cur / c
            if nxt.is_symlink():
                raise ApiError(409, "unsafe_path", "A folder in the destination is unsafe.")
            nxt.mkdir(exist_ok=True)
            if root not in nxt.resolve().parents and nxt.resolve() != root:
                raise ApiError(409, "unsafe_path", "A folder in the destination is unsafe.")
            cur = nxt
        for cand in candidate_names(safe):
            target = cur / cand
            try:
                os.link(partial_path, target)
            except FileExistsError:
                continue
            except OSError as e:
                raise ApiError(500, "finalize_failed", "Could not finalise file.") from e
            os.unlink(partial_path)
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

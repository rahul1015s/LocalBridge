"""User settings stored in ``$XDG_CONFIG_HOME/localbridge/settings.json`` (validated on load)."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "localbridge"


def default_destination() -> Path:
    dl = Path.home() / "Downloads"
    return (dl if dl.is_dir() else Path.home()) / "LocalBridge"


@dataclass
class Settings:
    device_name: str = field(default_factory=lambda: socket.gethostname()[:64] or "Fedora")
    destination: str = field(default_factory=lambda: str(default_destination()))
    interface: str = ""  # last used interface name; empty = first available

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or config_dir() / "settings.json"
        s = cls()
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return s
        if not isinstance(raw, dict):
            return s
        for key in ("device_name", "destination", "interface"):
            v = raw.get(key)
            if isinstance(v, str) and len(v) < 4096:
                setattr(s, key, v)
        s.device_name = s.device_name.strip()[:64] or cls().device_name
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "settings.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, path)

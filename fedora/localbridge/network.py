"""Local interface discovery (no multicast, no external lookups)."""

from __future__ import annotations

import ipaddress
import json
import subprocess  # noqa: S404


def local_ipv4_addresses() -> list[tuple[str, str]]:
    """Return ``(interface, address)`` for non-loopback, non-link-local IPv4 addresses."""
    try:
        out = subprocess.run(  # noqa: S603
            ["ip", "-j", "-4", "addr", "show", "up"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
        result = []
        for itf in json.loads(out):
            for a in itf.get("addr_info", []):
                ip = ipaddress.ip_address(a["local"])
                if not (ip.is_loopback or ip.is_link_local):
                    result.append((itf["ifname"], str(ip)))
        return result
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []

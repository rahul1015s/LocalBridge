"""Local interface discovery (no multicast, no external lookups). Works on Linux, macOS, Windows."""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess  # noqa: S404


def _via_psutil() -> list[tuple[str, str]] | None:
    try:
        import psutil
    except ImportError:
        return None
    stats = psutil.net_if_stats()
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for a in addrs:
            if a.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(a.address)
            if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
                out.append((name, str(ip)))
    return out


def _via_ip_command() -> list[tuple[str, str]]:
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


def local_ipv4_addresses() -> list[tuple[str, str]]:
    """Return ``(interface, address)`` for up, non-loopback, non-link-local IPv4 addresses."""
    found = _via_psutil()
    return found if found is not None else _via_ip_command()

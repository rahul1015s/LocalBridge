"""mDNS/DNS-SD advertisement so the phone can *locate* this receiver.

Discovery never grants trust. It only helps the phone find the host/port when the address in the
QR code isn't reachable, and tells it a computer is announcing itself. Pairing still requires the QR
secret and the certificate fingerprint pin. Only a random per-run receiver id is advertised (no
name, no fingerprint, no secret), and only on the interface the receiver is bound to.
"""

from __future__ import annotations

import logging
import socket
import threading

log = logging.getLogger(__name__)

SERVICE_TYPE = "_localbridge._tcp.local."


class Advertiser:
    def __init__(self, host: str, port: int, rid: str):
        self.host, self.port, self.rid = host, port, rid
        self._zc = None
        self._info = None
        self._lock = threading.Lock()
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start_async(self) -> None:
        """Registering takes ~2 s (probe + announce), so never block the caller."""
        self._thread = threading.Thread(target=self.start, name="mdns-advertise", daemon=True)
        self._thread.start()

    def start(self) -> bool:
        """Returns False (and logs) if zeroconf is missing or the network refuses; never raises."""
        try:
            from zeroconf import ServiceInfo, Zeroconf

            zc = Zeroconf(interfaces=[self.host])  # only the bound interface
            info = ServiceInfo(
                SERVICE_TYPE,
                f"LocalBridge-{self.rid[:8]}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(self.host)],
                port=self.port,
                properties={"v": "1", "rid": self.rid},
            )
            zc.register_service(info)
            with self._lock:
                if self._stopped:  # stop() ran while we were registering
                    zc.unregister_service(info)
                    zc.close()
                    return False
                self._zc, self._info = zc, info
            return True
        except Exception:  # noqa: BLE001 - discovery is optional; the QR path always works
            log.warning("mDNS advertising unavailable", exc_info=True)
            return False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            zc, info = self._zc, self._info
            self._zc = self._info = None
        try:
            if zc is not None:
                if info is not None:
                    zc.unregister_service(info)
                zc.close()
        except Exception:  # noqa: BLE001
            log.debug("mDNS shutdown error", exc_info=True)

    @property
    def active(self) -> bool:
        return self._zc is not None

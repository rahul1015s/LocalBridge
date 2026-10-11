"""TEST TOOL ONLY: headless receiver that auto-confirms pairing and auto-approves transfers.

The product never does this (approval is always an explicit user action). This exists solely so the
cross-implementation interop test (Kotlin client <-> Python receiver) can run unattended.

Usage: headless_receiver.py HOST DEST_DIR URI_OUT_FILE
"""

import signal
import sys
import time
from pathlib import Path

from localbridge.models import TransferState
from localbridge.server import ReceiverConfig, ReceiverService


def main(host: str, dest: str, uri_out: str) -> None:
    Path(dest).mkdir(parents=True, exist_ok=True)
    svc = ReceiverService(ReceiverConfig(host, 0, Path(dest), "Interop Receiver"))
    core = svc.core
    core.pairing.subscribe(
        lambda e, p: core.pairing.decide(p.pairing_id, True) if e == "pairing_requested" else None
    )

    def on_transfer(t):
        if t.state == TransferState.AWAITING_APPROVAL:
            core.transfers.approve(t.transfer_id)

    core.transfers.subscribe(on_transfer)
    svc.start()
    Path(uri_out).write_text(svc.pairing_uri())
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    while not stop:
        time.sleep(0.2)
    svc.stop()


if __name__ == "__main__":
    main(*sys.argv[1:4])

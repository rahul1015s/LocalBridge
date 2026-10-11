"""TEST TOOL ONLY: headless receiver that auto-confirms pairing and auto-approves transfers.

The product never does this (approval is always an explicit user action). This exists solely so the
cross-implementation interop test (Kotlin client <-> Python receiver) can run unattended.

Usage: headless_receiver.py HOST DEST_DIR URI_OUT_FILE [FILE_TO_OFFER_TO_PHONES]
"""

import signal
import sys
import time
from pathlib import Path

from localbridge.models import TransferState
from localbridge.server import ReceiverConfig, ReceiverService


def main(host: str, dest: str, uri_out: str, offer: str | None = None) -> None:
    Path(dest).mkdir(parents=True, exist_ok=True)
    svc = ReceiverService(ReceiverConfig(host, 0, Path(dest), "Interop Receiver"))
    core = svc.core

    def on_pair(event, p):
        if event == "pairing_requested":
            core.pairing.decide(p.pairing_id, True)
            # Codes are single-use: publish a fresh one for the next test.
            Path(uri_out).write_text(svc.pairing_uri())

    def on_session(event, session):
        if event == "session_created" and offer:
            core.outbox.add_file(session.session_id, Path(offer))
            core.outbox.add_text(session.session_id, "hello from the laptop")

    core.pairing.subscribe(on_pair)
    core.pairing.subscribe(on_session)
    core.subscribe_messages(lambda m: Path(dest, "message.txt").write_text(m.text))

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
    main(*sys.argv[1:5])

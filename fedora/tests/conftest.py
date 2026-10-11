from __future__ import annotations

import pytest

from localbridge.models import Limits
from localbridge.server import ReceiverConfig, ReceiverService

from .helpers import PinnedClient, parse_pairing_uri


class Harness:
    def __init__(self, svc: ReceiverService, dest):
        self.svc, self.dest = svc, dest
        self.auto_confirm = True
        svc.core.pairing.subscribe(self._on_pair_event)

    def _on_pair_event(self, event, payload):
        if event == "pairing_requested" and self.auto_confirm is not None:
            self.svc.core.pairing.decide(payload.pairing_id, bool(self.auto_confirm))

    def client(self, token=None, fp=None) -> PinnedClient:
        return PinnedClient("127.0.0.1", self.svc.port, fp or self.svc.identity.fingerprint, token)

    def pair(self, name="Pixel") -> PinnedClient:
        q = parse_pairing_uri(self.svc.pairing_uri())
        c = self.client()
        st, data = c.request(
            "POST",
            "/v1/pair",
            json_body={
                "protocol": 1,
                "session_id": q["sid"],
                "secret": q["secret"],
                "device_name": name,
            },
        )
        assert st == 200, data
        return self.client(token=data["token"])

    def new_transfer(self, c, files):
        st, data = c.request("POST", "/v1/transfers", json_body={"files": files})
        assert st == 201, data
        return data


def make_harness(tmp_path, limits: Limits | None = None) -> Harness:
    dest = tmp_path / "inbox"
    dest.mkdir()
    cfg = ReceiverConfig(
        "127.0.0.1", 0, dest, "TestBox", limits or Limits(min_free_space_margin=0), advertise=False
    )
    svc = ReceiverService(cfg)
    svc.start()
    return Harness(svc, dest)


@pytest.fixture
def h(tmp_path):
    harness = make_harness(tmp_path)
    yield harness
    harness.svc.stop()

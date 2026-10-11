"""Tk GUI smoke test. Needs tkinter and a display; skipped otherwise."""

import hashlib
import os
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")

from localbridge.settings import Settings  # noqa: E402
from localbridge.ui_tk import TkApp  # noqa: E402

from .helpers import PinnedClient, parse_pairing_uri  # noqa: E402


@pytest.fixture
def app(tmp_path):
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and os.name != "nt":
        pytest.skip("no display")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("cannot open a display")
    a = TkApp(
        root, Settings(device_name="Tk Box", destination=str(tmp_path / "in"), advertise=False)
    )
    a.ifaces = [("lo", "127.0.0.1")]
    a.iface.config(values=["127.0.0.1  (lo)"])
    a.iface.current(0)
    a.ask_yes_no = lambda title, text: True
    a._render()
    yield a
    if a.running:
        a.stop()
    root.destroy()


def pump(app, secs=0.0, until=None, limit=15):
    end = time.time() + (secs or limit)
    while time.time() < end:
        app.root.update()
        if until and until():
            return True
        time.sleep(0.02)
    return until is None


def sha(b):
    return hashlib.sha256(b).hexdigest()


def test_tk_full_flow(app, tmp_path):
    app.start()
    assert app.running and app._qr_image is not None and app.start_btn["text"] == "Stop receiving"
    q = parse_pairing_uri(app.svc.pairing_uri())
    c = PinnedClient("127.0.0.1", app.svc.port, q["fp"])
    out = {}
    th = threading.Thread(
        target=lambda: out.update(
            r=c.request(
                "POST",
                "/v1/pair",
                json_body={
                    "protocol": 1,
                    "session_id": q["sid"],
                    "secret": q["secret"],
                    "device_name": "Phone",
                },
            )
        )
    )
    th.start()
    assert pump(app, until=lambda: "r" in out)
    th.join()
    assert out["r"][0] == 200
    c.token = out["r"][1]["token"]
    pump(app, 0.6)
    assert app.dev_list.size() == 1

    files = [{"name": "a.bin", "size": 3, "dir": "Trip/sub"}, {"name": "b.txt", "size": 2}]
    box = {}
    th = threading.Thread(
        target=lambda: box.update(r=c.request("POST", "/v1/transfers", json_body={"files": files}))
    )
    th.start()
    assert pump(app, until=lambda: "r" in box)  # approval dialog auto-answered yes
    tr = box["r"][1]

    def approved():
        return c.request("GET", f"/v1/transfers/{tr['transfer_id']}")[1]["state"] == "APPROVED"

    assert pump(app, until=approved)  # the sender polls for the decision, like the phone does
    for i, data in enumerate([b"abc", b"hi"]):
        fid = tr["files"][i]["file_id"]
        res: dict = {}
        th = threading.Thread(
            target=lambda f=fid, d=data: res.update(
                put=c.request(
                    "PUT", f"/v1/transfers/{tr['transfer_id']}/files/{f}/content", body=d
                ),
                fin=c.request(
                    "POST",
                    f"/v1/transfers/{tr['transfer_id']}/files/{f}/finalize",
                    json_body={"sha256": sha(d), "size": len(d)},
                ),
            )
        )
        th.start()
        assert pump(app, until=lambda r=res: "fin" in r)
        assert res["fin"][0] == 200
    pump(app, 0.6)
    assert (
        app.settings.destination
        and (tmp_path / "in" / "Trip" / "sub" / "a.bin").read_bytes() == b"abc"
    )
    assert (tmp_path / "in" / "b.txt").read_bytes() == b"hi"
    assert app.tree.item(tr["transfer_id"], "values")[0] == "Completed"

    # phone -> computer text
    c.request("POST", "/v1/messages", json_body={"text": "link https://example.org"})
    pump(app, 0.8)
    assert app.msg_list.size() == 1

    # computer -> phone: text and a folder
    app.text_var.set("hello phone")
    app.send_text()
    d = tmp_path / "Pics"
    d.mkdir()
    (d / "p.jpg").write_bytes(b"jpg")
    app.queue_paths([str(d)])
    pump(app, 0.8)
    items = c.request("GET", "/v1/outbox")[1]["items"]
    assert {i["kind"] for i in items} == {"text", "file"}
    assert any(i["dir"] == "Pics" for i in items)
    assert len(app.out_tree.get_children()) == 2

    # stop really stops listening
    port = app.svc.port
    app.stop()
    assert not app.running and app.start_btn["text"] == "Start receiving"
    import socket

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


def test_tk_pairing_denied_and_rejected_transfer(app, tmp_path):
    app.ask_yes_no = lambda title, text: False
    app.start()
    q = parse_pairing_uri(app.svc.pairing_uri())
    c = PinnedClient("127.0.0.1", app.svc.port, q["fp"])
    out = {}
    th = threading.Thread(
        target=lambda: out.update(
            r=c.request(
                "POST",
                "/v1/pair",
                json_body={
                    "protocol": 1,
                    "session_id": q["sid"],
                    "secret": q["secret"],
                    "device_name": "Phone",
                },
            )
        )
    )
    th.start()
    assert pump(app, until=lambda: "r" in out)
    th.join()
    assert out["r"][0] == 403
    assert app.dev_list.size() == 0

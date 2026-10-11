"""Resume, silent-stream takeover, messages and computer->phone outbox (real TLS, loopback)."""

import hashlib
import socket
import ssl
import time

from localbridge.models import Limits

from .conftest import make_harness


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def raw_put(h, c, t, idx, body_len, send: bytes, offset=0):
    """Open a PUT and send only part of the body; return the still-open socket."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    tls = ctx.wrap_socket(socket.create_connection(("127.0.0.1", h.svc.port)), server_hostname="x")
    fid = t["files"][idx]["file_id"]
    tls.sendall(
        (
            f"PUT /v1/transfers/{t['transfer_id']}/files/{fid}/content HTTP/1.1\r\nHost: x\r\n"
            f"Authorization: Bearer {c.token}\r\nContent-Length: {body_len}\r\n"
            f"X-LocalBridge-Offset: {offset}\r\n\r\n"
        ).encode()
        + send
    )
    return tls


def file_view(c, t, idx=0):
    return c.request("GET", f"/v1/transfers/{t['transfer_id']}")[1]["files"][idx]


def wait_state(c, t, want, idx=0, secs=6):
    end = time.time() + secs
    while time.time() < end:
        v = file_view(c, t, idx)
        if v["state"] == want:
            return v
        time.sleep(0.1)
    raise AssertionError(f"never reached {want}: {file_view(c, t, idx)}")


def put(c, t, idx, data, offset=0):
    fid = t["files"][idx]["file_id"]
    return c.request(
        "PUT",
        f"/v1/transfers/{t['transfer_id']}/files/{fid}/content",
        body=data,
        headers={"X-LocalBridge-Offset": str(offset)},
    )


def fin(c, t, idx, data):
    fid = t["files"][idx]["file_id"]
    return c.request(
        "POST",
        f"/v1/transfers/{t['transfer_id']}/files/{fid}/finalize",
        json_body={"sha256": sha(data), "size": len(data)},
    )


DATA = bytes(range(256)) * 20_000  # ~5 MB, non-repeating-looking


def test_resume_after_connection_drop_yields_identical_file(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "big.bin", "size": len(DATA)}])
    h.svc.core.transfers.approve(t["transfer_id"])
    s = raw_put(h, c, t, 0, len(DATA), DATA[: 3 * 1024 * 1024])
    time.sleep(0.8)
    s.close()
    v = wait_state(c, t, "INTERRUPTED")
    assert 0 < v["received"] < len(DATA)
    off = v["received"]
    st, r = put(c, t, 0, DATA[off:], offset=off)
    assert st == 200 and r["sha256"] == sha(DATA)  # server hash covers the whole file
    st, res = fin(c, t, 0, DATA)
    assert st == 200 and res["state"] == "COMPLETED"
    assert (h.dest / "big.bin").read_bytes() == DATA


def test_wrong_offset_is_refused_and_keeps_the_partial(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "big.bin", "size": len(DATA)}])
    h.svc.core.transfers.approve(t["transfer_id"])
    s = raw_put(h, c, t, 0, len(DATA), DATA[: 3 * 1024 * 1024])
    time.sleep(0.8)
    s.close()
    v = wait_state(c, t, "INTERRUPTED")
    st, data = put(c, t, 0, DATA[1000:], offset=1000)
    assert st == 409 and data["error"]["code"] == "offset_mismatch"
    assert wait_state(c, t, "INTERRUPTED")["received"] == v["received"]  # still resumable
    st, _ = put(c, t, 0, DATA[v["received"] :], offset=v["received"])
    assert st == 200
    assert fin(c, t, 0, DATA)[0] == 200


def test_offset_without_partial_is_refused(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.bin", "size": 10}])
    h.svc.core.transfers.approve(t["transfer_id"])
    st, data = put(c, t, 0, b"x" * 5, offset=5)
    assert st == 409 and data["error"]["code"] == "offset_mismatch"
    assert put(c, t, 0, b"0123456789")[0] == 200  # a clean restart still works


def test_restart_from_zero_replaces_old_partial(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "big.bin", "size": len(DATA)}])
    h.svc.core.transfers.approve(t["transfer_id"])
    s = raw_put(h, c, t, 0, len(DATA), DATA[: 2 * 1024 * 1024])
    time.sleep(0.6)
    s.close()
    wait_state(c, t, "INTERRUPTED")
    assert put(c, t, 0, DATA)[0] == 200
    assert fin(c, t, 0, DATA)[0] == 200
    assert (h.dest / "big.bin").read_bytes() == DATA


def test_silent_stream_is_superseded_by_a_new_one(tmp_path):
    hh = make_harness(tmp_path, Limits(takeover_idle_seconds=1, min_free_space_margin=0))
    try:
        c = hh.pair()
        t = hh.new_transfer(c, [{"name": "big.bin", "size": len(DATA)}])
        hh.svc.core.transfers.approve(t["transfer_id"])
        dead = raw_put(hh, c, t, 0, len(DATA), DATA[: 2 * 1024 * 1024])  # then goes silent
        time.sleep(2.5)
        st, data = put(c, t, 0, DATA)  # restart takes over; old socket still open
        assert st == 200, data
        assert fin(c, t, 0, DATA)[0] == 200
        dead.close()
        assert (hh.dest / "big.bin").read_bytes() == DATA
        time.sleep(0.5)
        assert file_view(c, t)["state"] == "DONE"  # the dead stream did not clobber state
    finally:
        hh.svc.stop()


def test_active_stream_is_not_superseded(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "big.bin", "size": len(DATA)}])
    h.svc.core.transfers.approve(t["transfer_id"])
    live = raw_put(h, c, t, 0, len(DATA), DATA[: 2 * 1024 * 1024])
    time.sleep(0.5)
    st, data = put(c, t, 0, DATA)
    live.close()
    assert st == 409 and data["error"]["code"] == "file_state"


def test_interrupted_transfer_expires_after_resume_window(tmp_path):
    hh = make_harness(tmp_path, Limits(resume_window_seconds=1, min_free_space_margin=0))
    try:
        c = hh.pair()
        t = hh.new_transfer(c, [{"name": "big.bin", "size": len(DATA)}])
        hh.svc.core.transfers.approve(t["transfer_id"])
        s = raw_put(hh, c, t, 0, len(DATA), DATA[: 2 * 1024 * 1024])
        time.sleep(0.6)
        s.close()
        wait_state(c, t, "INTERRUPTED")
        time.sleep(1.6)
        assert c.request("GET", f"/v1/transfers/{t['transfer_id']}")[1]["state"] == "FAILED"
        part = hh.dest / ".localbridge-partial"
        assert not part.exists() or not any(part.iterdir())  # partial cleaned up
    finally:
        hh.svc.stop()


# ---- messages ------------------------------------------------------------------------------
def test_message_delivered_to_desktop(h):
    got = []
    h.svc.core.subscribe_messages(got.append)
    c = h.pair("Pixel")
    st, _ = c.request("POST", "/v1/messages", json_body={"text": "https://example.com/x"})
    assert st == 201 and got[0].text == "https://example.com/x" and got[0].device_name == "Pixel"


def test_message_validation_and_auth(h):
    c = h.pair()
    assert c.request("POST", "/v1/messages", json_body={"text": ""})[0] == 422
    assert c.request("POST", "/v1/messages", json_body={"text": "x" * 70_000})[0] in (413, 422)
    assert c.request("POST", "/v1/messages", json_body={"text": "hi"}, token=None)[0] == 401


# ---- outbox (computer -> phone) -----------------------------------------------------------------
def queue_file(h, c, tmp_path, data=b"hello phone" * 1000, name="doc.txt"):
    f = tmp_path / name
    f.write_bytes(data)
    sid = h.svc.core.pairing.authenticate(c.token).session_id
    item = h.svc.core.outbox.add_file(sid, f)
    end = time.time() + 5
    while item.state != "READY" and time.time() < end:
        time.sleep(0.05)
    assert item.state == "READY"
    return item, data


def test_outbox_download_with_hash_and_range(h, tmp_path):
    c = h.pair()
    item, data = queue_file(h, c, tmp_path)
    st, lst = c.request("GET", "/v1/outbox")
    assert st == 200 and lst["items"][0]["sha256"] == sha(data) and "path" not in str(lst)
    st, body = c.request("GET", f"/v1/outbox/{item.item_id}/content")
    assert st == 200 and body == data
    st, part = c.request(
        "GET", f"/v1/outbox/{item.item_id}/content", headers={"Range": "bytes=100-"}
    )
    assert st == 206 and part == data[100:]
    st, _ = c.request("POST", f"/v1/outbox/{item.item_id}/done", json_body={"status": "saved"})
    assert st == 200 and item.state == "DELIVERED"
    assert c.request("GET", "/v1/outbox")[1]["items"] == []


def test_outbox_is_session_bound_and_authenticated(h, tmp_path):
    a, b = h.pair("A"), h.pair("B")
    item, _ = queue_file(h, a, tmp_path)
    assert b.request("GET", "/v1/outbox")[1]["items"] == []
    assert b.request("GET", f"/v1/outbox/{item.item_id}/content")[0] == 404
    assert (
        b.request("POST", f"/v1/outbox/{item.item_id}/done", json_body={"status": "saved"})[0]
        == 404
    )
    assert a.request("GET", f"/v1/outbox/{item.item_id}/content", token=None)[0] == 401
    assert a.request("GET", "/v1/outbox/not-an-id/content")[0] == 404


def test_outbox_file_changed_after_queueing_is_refused(h, tmp_path):
    c = h.pair()
    item, data = queue_file(h, c, tmp_path)
    (tmp_path / "doc.txt").write_bytes(data + b"tampered")
    st, d = c.request("GET", f"/v1/outbox/{item.item_id}/content")
    assert st == 409 and d["error"]["code"] == "file_changed"


def test_outbox_text_and_withdraw(h):
    c = h.pair()
    sid = h.svc.core.pairing.authenticate(c.token).session_id
    t = h.svc.core.outbox.add_text(sid, "copy me")
    assert c.request("GET", "/v1/outbox")[1]["items"][0]["text"] == "copy me"
    h.svc.core.outbox.cancel(t.item_id)
    assert c.request("GET", "/v1/outbox")[1]["items"] == []


def test_outbox_rejects_non_files_and_caps(h, tmp_path):
    import pytest

    from localbridge.models import ApiError

    c = h.pair()
    sid = h.svc.core.pairing.authenticate(c.token).session_id
    with pytest.raises(ApiError):
        h.svc.core.outbox.add_file(sid, tmp_path)  # a directory
    with pytest.raises(ApiError):
        h.svc.core.outbox.add_text(sid, "x" * 70_000)


def test_stop_withdraws_outbox_items(tmp_path):
    hh = make_harness(tmp_path)
    c = hh.pair()
    sid = hh.svc.core.pairing.authenticate(c.token).session_id
    item = hh.svc.core.outbox.add_text(sid, "bye")
    hh.svc.stop()
    assert item.state == "DECLINED"

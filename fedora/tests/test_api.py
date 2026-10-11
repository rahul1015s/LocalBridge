"""End-to-end tests against the real HTTPS server on 127.0.0.1.

NOTE: these run over loopback only. They prove protocol/security logic, NOT
mobile-hotspot compatibility.
"""

import hashlib
import socket
import threading
import time

import pytest

from localbridge.models import Limits

from .conftest import make_harness
from .helpers import PinMismatch, parse_pairing_uri


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def upload(c, t, idx, data: bytes, name_ok=True):
    fid = t["files"][idx]["file_id"]
    return c.request("PUT", f"/v1/transfers/{t['transfer_id']}/files/{fid}/content", body=data)


def finalize(c, t, idx, data: bytes, digest=None):
    fid = t["files"][idx]["file_id"]
    return c.request(
        "POST",
        f"/v1/transfers/{t['transfer_id']}/files/{fid}/finalize",
        json_body={"sha256": digest or sha(data), "size": len(data)},
    )


def approve(h, t):
    h.svc.core.transfers.approve(t["transfer_id"])


# ---- TLS / pairing ----------------------------------------------------------------------
def test_health_is_minimal(h):
    st, data = h.client().request("GET", "/v1/health", token=None)
    assert st == 200 and set(data) == {"status", "protocol"}


def test_certificate_mismatch_fails_closed(h):
    with pytest.raises(PinMismatch):
        h.client(fp="0" * 64).request("GET", "/v1/health", token=None)


def test_no_docs_or_extra_web_endpoints(h):
    for path in ("/docs", "/redoc", "/openapi.json", "/"):
        st, _ = h.client().request("GET", path, token=None)
        assert st == 404


def test_plain_http_is_not_served(h):
    s = socket.create_connection(("127.0.0.1", h.svc.port), timeout=3)
    s.sendall(b"GET /v1/health HTTP/1.1\r\nHost: x\r\n\r\n")
    s.settimeout(3)
    try:
        resp = s.recv(100)
    except (ConnectionResetError, TimeoutError):
        resp = b""
    s.close()
    assert not resp.startswith(b"HTTP/1.1 200")


def test_pairing_requires_valid_credentials_and_is_single_use(h):
    q = parse_pairing_uri(h.svc.pairing_uri())
    body = {"protocol": 1, "session_id": q["sid"], "secret": q["secret"], "device_name": "A"}
    c = h.client()
    st, data = c.request("POST", "/v1/pair", json_body={**body, "secret": "z" * 43})
    assert st == 401
    st, data = c.request("POST", "/v1/pair", json_body=body)
    assert st == 200 and data["token"]
    st, _ = c.request("POST", "/v1/pair", json_body=body)  # replay
    assert st == 401


def test_pairing_denied_by_user(h):
    h.auto_confirm = False
    q = parse_pairing_uri(h.svc.pairing_uri())
    st, data = h.client().request(
        "POST",
        "/v1/pair",
        json_body={
            "protocol": 1,
            "session_id": q["sid"],
            "secret": q["secret"],
            "device_name": "A",
        },
    )
    assert st == 403 and data["error"]["code"] == "pairing_denied"


def test_failed_pairing_is_rate_limited(h):
    c = h.client()
    codes = []
    for _ in range(8):
        st, _ = c.request(
            "POST",
            "/v1/pair",
            json_body={
                "protocol": 1,
                "session_id": "a" * 16,
                "secret": "b" * 32,
                "device_name": "x",
            },
        )
        codes.append(st)
    assert codes[:5] == [401] * 5 and codes[5:] == [429] * 3


def test_error_responses_do_not_echo_input(h):
    st, data = h.client().request(
        "POST", "/v1/pair", json_body={"protocol": 1, "secret": "SENSITIVE-VALUE", "bogus": 1}
    )
    assert st == 422 and "SENSITIVE" not in str(data)


# ---- authn / authz -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/transfers"),
        ("GET", "/v1/transfers/abcdefgh12345678"),
        ("POST", "/v1/transfers/abcdefgh12345678/cancel"),
        ("PUT", "/v1/transfers/abcdefgh12345678/files/abcdefgh12345678/content"),
        ("POST", "/v1/transfers/abcdefgh12345678/files/abcdefgh12345678/finalize"),
    ],
)
def test_unauthenticated_requests_rejected(h, method, path):
    c = h.client()
    assert c.request(method, path, token=None)[0] == 401
    assert c.request(method, path, token="bogus-token")[0] == 401


def test_revoked_session_is_rejected(h):
    c = h.pair()
    assert (
        c.request("POST", "/v1/transfers", json_body={"files": [{"name": "a", "size": 1}]})[0]
        == 201
    )
    h.svc.core.pairing.revoke_all()
    assert (
        c.request("POST", "/v1/transfers", json_body={"files": [{"name": "a", "size": 1}]})[0]
        == 401
    )


def test_other_session_cannot_see_or_cancel_transfer(h):
    a = h.pair("A")
    b = h.pair("B")
    t = h.new_transfer(a, [{"name": "a.txt", "size": 1}])
    assert b.request("GET", f"/v1/transfers/{t['transfer_id']}")[0] == 404
    assert b.request("POST", f"/v1/transfers/{t['transfer_id']}/cancel")[0] == 404


def test_sender_cannot_approve_remotely(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.txt", "size": 1}])
    for path in ("approve", "reject"):
        st, _ = c.request("POST", f"/v1/transfers/{t['transfer_id']}/{path}")
        assert st in (404, 405)
    assert c.request("GET", f"/v1/transfers/{t['transfer_id']}")[1]["state"] == "AWAITING_APPROVAL"


# ---- transfers ---------------------------------------------------------------------------------
def test_content_before_approval_is_refused_and_writes_nothing(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.txt", "size": 5}])
    st, data = upload(c, t, 0, b"hello")
    assert st == 409 and data["error"]["code"] == "not_approved"
    assert not [p for p in h.dest.iterdir() if p.name != ".localbridge-partial"]


def test_rejected_transfer_never_accepts_content(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.txt", "size": 5}])
    h.svc.core.transfers.reject(t["transfer_id"])
    assert upload(c, t, 0, b"hello")[0] == 409
    assert list(h.dest.glob("a*")) == []


def test_full_transfer_with_integrity(h):
    c = h.pair()
    payload = b"\x00\x01video-ish bytes" * 100_000
    t = h.new_transfer(c, [{"name": "clip.mp4", "size": len(payload)}])
    approve(h, t)
    st, data = upload(c, t, 0, payload)
    assert st == 200 and data["sha256"] == sha(payload)
    st, data = finalize(c, t, 0, payload)
    assert st == 200 and data["state"] == "COMPLETED"
    assert (h.dest / "clip.mp4").read_bytes() == payload
    assert not (h.dest / ".localbridge-partial").exists() or not any(
        (h.dest / ".localbridge-partial").iterdir()
    )


def test_multiple_files_and_duplicate_names(h):
    c = h.pair()
    files = [
        {"name": "a.txt", "size": 1},
        {"name": "a.txt", "size": 2},
        {"name": "b.bin", "size": 0},
    ]
    t = h.new_transfer(c, files)
    approve(h, t)
    for i, data in enumerate([b"1", b"22", b""]):
        assert upload(c, t, i, data)[0] == 200
        st, res = finalize(c, t, i, data)
        assert st == 200
    assert res["state"] == "COMPLETED"
    assert sorted(p.name for p in h.dest.iterdir() if p.is_file()) == [
        "a (1).txt",
        "a.txt",
        "b.bin",
    ]


def test_wrong_hash_fails_and_leaves_no_file(h):
    c = h.pair()
    data = b"important"
    t = h.new_transfer(c, [{"name": "doc.pdf", "size": len(data)}])
    approve(h, t)
    assert upload(c, t, 0, data)[0] == 200
    st, res = finalize(c, t, 0, data, digest=sha(b"something else"))
    assert st == 422 and res["error"]["code"] == "integrity_failed"
    assert not (h.dest / "doc.pdf").exists()
    assert c.request("GET", f"/v1/transfers/{t['transfer_id']}")[1]["state"] == "FAILED"


def test_size_mismatch_rejected(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.bin", "size": 10}])
    approve(h, t)
    st, data = upload(c, t, 0, b"12345")
    assert st == 400 and data["error"]["code"] == "size_mismatch"
    assert not (h.dest / "a.bin").exists()


def test_unknown_size_streaming_accepted(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "s.bin"}])
    approve(h, t)
    assert upload(c, t, 0, b"abc")[0] == 200
    assert finalize(c, t, 0, b"abc")[0] == 200


def test_path_traversal_filename_is_confined(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "../../../../tmp/evil.sh", "size": 1}])
    assert t["files"][0]["name"] == "evil.sh"
    approve(h, t)
    upload(c, t, 0, b"x")
    finalize(c, t, 0, b"x")
    assert (h.dest / "evil.sh").exists()
    assert not (h.dest.parent / "evil.sh").exists()


def test_malformed_metadata(h):
    c = h.pair()
    bad = [
        {"files": []},
        {"files": [{"name": "", "size": 1}]},
        {"files": [{"name": "a", "size": -1}]},
        {"files": [{"name": "a", "size": "big"}]},
        {"files": [{"name": "a", "size": 1, "extra": True}]},
        {"nope": 1},
    ]
    for body in bad:
        assert c.request("POST", "/v1/transfers", json_body=body)[0] == 422
    assert c.request("POST", "/v1/transfers", body=b"{not json")[0] == 422


def test_oversized_metadata_rejected(h):
    c = h.pair()
    big = b'{"files":[' + b'{"name":"a","size":1},' * 80_000 + b'{"name":"a","size":1}]}'
    st, _ = c.request("POST", "/v1/transfers", body=big)
    assert st == 413


def test_invalid_ids_are_404_not_500(h):
    c = h.pair()
    for tid in ("nope", "../../etc/passwd", "a" * 500):
        st, _ = c.request("GET", f"/v1/transfers/{tid}")
        assert st in (404, 422)
    t = h.new_transfer(c, [{"name": "a", "size": 1}])
    approve(h, t)
    st, _ = c.request(
        "PUT", f"/v1/transfers/{t['transfer_id']}/files/..%2F..%2Fx/content", body=b"x"
    )
    assert st == 404


def test_cancel_before_content_blocks_upload(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.bin", "size": 3}])
    approve(h, t)
    assert c.request("POST", f"/v1/transfers/{t['transfer_id']}/cancel")[1]["state"] == "CANCELLED"
    assert upload(c, t, 0, b"abc")[0] == 409
    assert not (h.dest / "a.bin").exists()


def test_cancel_mid_transfer_leaves_no_file(h):
    c = h.pair()
    size = 200 * 1024 * 1024
    t = h.new_transfer(c, [{"name": "big.bin", "size": size}])
    approve(h, t)
    result = {}

    def slow_upload():
        import http.client
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", h.svc.port, context=ctx, timeout=30)
        conn.putrequest(
            "PUT", f"/v1/transfers/{t['transfer_id']}/files/{t['files'][0]['file_id']}/content"
        )
        conn.putheader("Authorization", f"Bearer {c.token}")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        try:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(200):
                conn.send(chunk)
                time.sleep(0.02)
            result["status"] = conn.getresponse().status
        except OSError as e:
            result["status"] = repr(e)
        finally:
            conn.close()

    th = threading.Thread(target=slow_upload)
    th.start()
    time.sleep(0.5)
    cc = h.pair("Other")  # cancel from the receiver side (UI cancel)
    _ = cc
    h.svc.core.transfers.cancel(t["transfer_id"], None, "Cancelled by receiver.")
    th.join(30)
    assert not th.is_alive()
    assert result["status"] != 200
    assert list(h.dest.glob("big*")) == []
    part = h.dest / ".localbridge-partial"
    assert not part.exists() or not any(part.iterdir())
    assert c.request("GET", f"/v1/transfers/{t['transfer_id']}")[1]["state"] == "CANCELLED"


def test_interrupted_upload_is_not_a_completed_file(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "cut.bin", "size": 1000}])
    approve(h, t)
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    raw = socket.create_connection(("127.0.0.1", h.svc.port))
    tls = ctx.wrap_socket(raw, server_hostname="127.0.0.1")
    fid = t["files"][0]["file_id"]
    tls.sendall(
        (
            f"PUT /v1/transfers/{t['transfer_id']}/files/{fid}/content HTTP/1.1\r\n"
            f"Host: x\r\nAuthorization: Bearer {c.token}\r\nContent-Length: 1000\r\n\r\n"
        ).encode()
        + b"A" * 100
    )
    time.sleep(0.3)
    tls.close()  # abrupt drop mid-body
    deadline = time.time() + 5
    state = None
    while time.time() < deadline:
        state = c.request("GET", f"/v1/transfers/{t['transfer_id']}")[1]["files"][0]["state"]
        if state == "FAILED":
            break
        time.sleep(0.1)
    assert state == "FAILED"
    assert not (h.dest / "cut.bin").exists()
    part = h.dest / ".localbridge-partial"
    assert not part.exists() or not any(part.iterdir())


def test_file_size_limit_enforced_at_stream_time(tmp_path):
    hh = make_harness(tmp_path, Limits(max_file_size=1000, min_free_space_margin=0))
    try:
        c = hh.pair()
        t = hh.new_transfer(c, [{"name": "u.bin"}])  # unknown declared size
        approve(hh, t)
        st, data = upload(c, t, 0, b"x" * 5000)
        assert st == 413
        assert not (hh.dest / "u.bin").exists()
    finally:
        hh.svc.stop()


def test_declared_size_over_limit_rejected_at_create(tmp_path):
    hh = make_harness(tmp_path, Limits(max_file_size=1000, min_free_space_margin=0))
    try:
        c = hh.pair()
        st, data = c.request(
            "POST", "/v1/transfers", json_body={"files": [{"name": "a", "size": 5000}]}
        )
        assert st == 413 and data["error"]["code"] == "file_too_large"
    finally:
        hh.svc.stop()


def test_insufficient_disk_space_refused(tmp_path):
    hh = make_harness(tmp_path, Limits(min_free_space_margin=10**18))
    try:
        c = hh.pair()
        t = hh.new_transfer(c, [{"name": "a.bin", "size": 10}])
        approve(hh, t)
        st, data = upload(c, t, 0, b"x" * 10)
        assert st == 507 and data["error"]["code"] == "insufficient_storage"
    finally:
        hh.svc.stop()


def test_unwritable_destination_reports_error(h):
    c = h.pair()
    t = h.new_transfer(c, [{"name": "a.bin", "size": 1}])
    approve(h, t)
    h.dest.chmod(0o500)
    try:
        st, data = upload(c, t, 0, b"x")
    finally:
        h.dest.chmod(0o700)
    assert st == 507


# ---- shutdown --------------------------------------------------------------------------------------
def test_receiver_stops_listening_and_cleans_up(tmp_path):
    hh = make_harness(tmp_path)
    port = hh.svc.port
    c = hh.pair()
    t = hh.new_transfer(c, [{"name": "a.bin", "size": 1}])
    approve(hh, t)
    hh.svc.stop()
    assert not hh.svc.running
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    assert not (hh.dest / ".localbridge-partial").exists()
    assert hh.svc.core.pairing.sessions() == []


def test_restart_gets_new_identity_and_old_pairing_is_useless(tmp_path):
    hh = make_harness(tmp_path)
    fp1 = hh.svc.identity.fingerprint
    old_uri = hh.svc.pairing_uri()
    hh.svc.stop()
    hh.svc.start()
    try:
        assert hh.svc.identity.fingerprint != fp1
        q = parse_pairing_uri(old_uri)
        st, _ = hh.client().request(
            "POST",
            "/v1/pair",
            json_body={
                "protocol": 1,
                "session_id": q["sid"],
                "secret": q["secret"],
                "device_name": "x",
            },
        )
        assert st == 401
    finally:
        hh.svc.stop()

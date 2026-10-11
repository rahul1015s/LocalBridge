"""Drive the terminal receiver as a real subprocess: pairing, approval, transfer, outbox, quit."""

import hashlib
import queue
import socket
import subprocess
import sys
import threading
import time

import pytest

from .helpers import PinnedClient, parse_pairing_uri


class Proc:
    def __init__(self, dest):
        self.p = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "localbridge",
                "--cli",
                "--host",
                "127.0.0.1",
                "--dest",
                str(dest),
                "--name",
                "CLI Box",
                "--no-advertise",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.q: queue.Queue[str] = queue.Queue()
        self.log: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.log.append(line)
            self.q.put(line)

    def expect(self, text, secs=15):
        end = time.time() + secs
        while time.time() < end:
            try:
                line = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if text in line:
                return line
        raise AssertionError(f"never saw {text!r}; got:\n{''.join(self.log)}")

    def send(self, s):
        self.p.stdin.write(s + "\n")
        self.p.stdin.flush()


@pytest.fixture
def cli(tmp_path):
    dest = tmp_path / "inbox"
    proc = Proc(dest)
    yield proc, dest
    if proc.p.poll() is None:
        proc.p.kill()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def test_cli_end_to_end(cli, tmp_path):
    proc, dest = cli
    line = proc.expect("paste: localbridge://")
    q = parse_pairing_uri(line.split("paste: ", 1)[1].strip())
    port = int(q["port"])
    c = PinnedClient("127.0.0.1", port, q["fp"])

    # pairing requires a typed yes
    res = {}
    t = threading.Thread(
        target=lambda: res.update(
            r=c.request(
                "POST",
                "/v1/pair",
                json_body={
                    "protocol": 1,
                    "session_id": q["sid"],
                    "secret": q["secret"],
                    "device_name": "Test Phone",
                },
            )
        )
    )
    t.start()
    proc.expect("Pair with “Test Phone”")
    proc.send("y")
    t.join(10)
    st, data = res["r"]
    assert st == 200
    c.token = data["token"]
    proc.expect("Connected: Test Phone")

    # a transfer: folder + loose file, declined first then accepted
    payload = {
        "files": [{"name": "a.bin", "size": 3, "dir": "Trip/day1"}, {"name": "b.txt", "size": 2}]
    }
    st, tr = c.request("POST", "/v1/transfers", json_body=payload)
    assert st == 201
    proc.expect("wants to send 2 file(s)")
    proc.send("n")
    proc.expect("Transfer rejected")
    assert (
        c.request(
            "PUT",
            f"/v1/transfers/{tr['transfer_id']}/files/{tr['files'][0]['file_id']}/content",
            body=b"abc",
        )[0]
        == 409
    )

    st, tr = c.request("POST", "/v1/transfers", json_body=payload)
    proc.expect("wants to send 2 file(s)")
    proc.send("y")
    for i, data_ in enumerate([b"abc", b"hi"]):
        fid = tr["files"][i]["file_id"]
        for _ in range(30):
            if c.request("GET", f"/v1/transfers/{tr['transfer_id']}")[1]["state"] == "APPROVED":
                break
            time.sleep(0.1)
        assert (
            c.request("PUT", f"/v1/transfers/{tr['transfer_id']}/files/{fid}/content", body=data_)[
                0
            ]
            == 200
        )
        assert (
            c.request(
                "POST",
                f"/v1/transfers/{tr['transfer_id']}/files/{fid}/finalize",
                json_body={"sha256": sha(data_), "size": len(data_)},
            )[0]
            == 200
        )
    proc.expect("Transfer completed")
    assert (dest / "Trip" / "day1" / "a.bin").read_bytes() == b"abc"
    assert (dest / "b.txt").read_bytes() == b"hi"

    # text from phone is shown
    c.request("POST", "/v1/messages", json_body={"text": "hello from phone"})
    proc.expect("Text from Test Phone: hello from phone")

    # computer -> phone
    f = tmp_path / "out.txt"
    f.write_text("to phone")
    proc.send(f"send {f}")
    proc.expect("Queued out.txt")
    proc.send("text see you soon")
    proc.expect("Queued text")
    for _ in range(50):
        items = c.request("GET", "/v1/outbox")[1]["items"]
        if len(items) == 2:
            break
        time.sleep(0.1)
    assert {i["kind"] for i in items} == {"file", "text"}

    # quit stops listening
    proc.send("quit")
    proc.expect("Receiver stopped")
    assert proc.p.wait(10) == 0
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


def test_cli_wrong_secret_never_prompts(cli):
    proc, _ = cli
    line = proc.expect("paste: localbridge://")
    q = parse_pairing_uri(line.split("paste: ", 1)[1].strip())
    c = PinnedClient("127.0.0.1", int(q["port"]), q["fp"])
    st, _ = c.request(
        "POST",
        "/v1/pair",
        json_body={
            "protocol": 1,
            "session_id": q["sid"],
            "secret": "x" * 43,
            "device_name": "Evil",
        },
    )
    assert st == 401
    time.sleep(0.5)
    assert not any("Pair with" in ln for ln in proc.log)
    proc.send("quit")
    proc.p.wait(10)


def test_cli_eof_shuts_down_cleanly(cli):
    proc, _ = cli
    proc.expect("paste: localbridge://")
    proc.p.stdin.close()
    assert proc.p.wait(10) == 0

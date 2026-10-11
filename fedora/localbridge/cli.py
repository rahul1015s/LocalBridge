"""Terminal receiver for any OS (Linux, macOS, Windows) and headless machines.

Pairing and every incoming transfer still require an explicit typed answer; there is no
auto-accept mode.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .models import ApiError, TransferState
from .network import local_ipv4_addresses
from .server import ReceiverConfig, ReceiverService
from .settings import Settings


def human(n: int | None) -> str:
    if n is None:
        return "unknown size"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1000 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1000
    return f"{n} B"


def qr_ascii(data: str) -> str:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


HELP = """commands:  new          show a new pairing code
           link         print the pairing link
           send <path>  send a file or folder to the phone (it must accept)
           text <msg>   send text or a link to the phone
           status       show devices and transfers
           quit         stop receiving and exit
"""


class Cli:
    def __init__(self, svc: ReceiverService, out=sys.stdout):
        self.svc = svc
        self.out = out
        self.events: queue.Queue = queue.Queue()
        self.prompts: deque = deque()  # (question, on_answer)
        self.prompted: set[str] = set()
        self.last_print: dict[str, float] = {}
        self.stop = threading.Event()

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    # -- wiring --------------------------------------------------------------------------
    def attach(self) -> None:
        c = self.svc.core
        c.pairing.subscribe(lambda e, p: self.events.put(("pair", e, p)))
        c.transfers.subscribe(lambda t: self.events.put(("transfer", t)))
        c.subscribe_messages(lambda m: self.events.put(("msg", m)))
        c.outbox.subscribe(lambda i: self.events.put(("outbox", i)))

    def banner(self) -> None:
        cfg = self.svc.core.cfg
        uri = self.svc.pairing_uri()
        self.say(
            f"Receiving on {cfg.host}:{self.svc.port} (encrypted). Saving to {cfg.destination}"
        )
        self.say(qr_ascii(uri))
        self.say(f"Scan with the LocalBridge app, or paste: {uri}")
        self.say(f"Fingerprint {self.svc.identity.fingerprint[:24].upper()}…  (code valid 2 min)")
        self.say("Type 'help' for commands. Nothing is saved until you approve it.\n")

    # -- event handling ---------------------------------------------------------------------
    def ask(self, question: str, on_answer) -> None:
        self.prompts.append((question, on_answer))
        if len(self.prompts) == 1:
            self.say(question + " [y/N] ")

    def handle_event(self, ev) -> None:
        kind = ev[0]
        if kind == "pair" and ev[1] == "pairing_requested":
            p = ev[2]
            self.ask(
                f"\nPair with “{p.device_name}” at {p.client_ip}? Only if this is your phone.",
                lambda yes, p=p: self.svc.core.pairing.decide(p.pairing_id, yes),
            )
        elif kind == "pair" and ev[1] == "session_created":
            self.say(f"Connected: {ev[2].device_name}")
        elif kind == "transfer":
            self.on_transfer(ev[1])
        elif kind == "msg":
            m = ev[1]
            self.say(f"\nText from {m.device_name}: {m.text}")
        elif kind == "outbox":
            i = ev[1]
            if i.state in ("DELIVERED", "DECLINED", "FAILED"):
                label = {"DELIVERED": "saved on phone", "DECLINED": "declined", "FAILED": "failed"}
                self.say(
                    f"To phone: {i.name if i.kind == 'file' else 'text'} — {label[i.state]}"
                    + (f" ({i.detail})" if i.detail else "")
                )

    def on_transfer(self, t) -> None:
        tid = t.transfer_id
        if t.state == TransferState.AWAITING_APPROVAL and tid not in self.prompted:
            self.prompted.add(tid)
            self.ask(self.describe(t), lambda yes, tid=tid: self.decide_transfer(tid, yes))
        elif t.state == TransferState.TRANSFERRING:
            now = time.monotonic()
            if now - self.last_print.get(tid, 0) > 2:
                self.last_print[tid] = now
                tot = t.total_declared
                pct = (
                    f"{100 * t.total_received // tot}%"
                    if t.size_known and tot
                    else human(t.total_received)
                )
                self.say(f"  receiving from {t.device_name}: {pct}")
        elif t.state in (
            TransferState.COMPLETED,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.REJECTED,
            TransferState.EXPIRED,
        ):
            if tid + t.state.value not in self.prompted:
                self.prompted.add(tid + t.state.value)
                self.say(
                    f"Transfer {t.state.value.lower()}" + (f": {t.reason}" if t.reason else "")
                )
                if t.state == TransferState.COMPLETED:
                    self.say(
                        f"  {len(t.files)} file(s) saved to {self.svc.core.cfg.destination} (SHA-256 verified)"
                    )

    def describe(self, t) -> str:
        folders: dict[str, list] = {}
        loose = []
        for f in t.files:
            (folders.setdefault(f.dirs[0], []) if f.dirs else loose).append(f)
        lines = [
            f"\n“{t.device_name}” wants to send {len(t.files)} file(s), "
            f"{human(t.total_declared) if t.size_known else 'size not declared'}:"
        ]
        for top, fs in list(folders.items())[:6]:
            lines.append(
                f"  [folder] {top}/  ({len(fs)} files, {human(sum(x.declared_size or 0 for x in fs))})"
            )
        for f in loose[:8]:
            lines.append(f"  {f.name}  ({human(f.declared_size)})")
        extra = len(folders) + len(loose) - min(len(folders), 6) - min(len(loose), 8)
        if extra > 0:
            lines.append(f"  …and {extra} more")
        lines.append(f"Save to {self.svc.core.cfg.destination}.\nAccept?")
        return "\n".join(lines)

    def decide_transfer(self, tid: str, yes: bool) -> None:
        try:
            (self.svc.core.transfers.approve if yes else self.svc.core.transfers.reject)(tid)
        except ApiError:
            self.say("That request is no longer active.")

    # -- commands ------------------------------------------------------------------------------
    def target_session(self):
        s = self.svc.core.pairing.sessions()
        return s[-1] if s else None

    def handle_line(self, line: str) -> None:
        line = line.strip()
        if self.prompts:
            _, cb = self.prompts.popleft()
            cb(line.lower() in ("y", "yes"))
            if self.prompts:
                self.say(self.prompts[0][0] + " [y/N] ")
            return
        cmd, _, arg = line.partition(" ")
        cmd = cmd.lower()
        if cmd in ("quit", "exit", "q"):
            self.stop.set()
        elif cmd == "help" or cmd == "":
            if cmd:
                self.say(HELP)
        elif cmd == "new":
            self.svc.core.pairing.new_offer()
            self.banner()
        elif cmd == "link":
            self.say(self.svc.pairing_uri())
        elif cmd == "status":
            self.say(
                f"devices: {[s.device_name for s in self.svc.core.pairing.sessions()] or 'none'}"
            )
            for t in self.svc.core.transfers.all():
                self.say(
                    f"  transfer {t.state.value}: {len(t.files)} file(s), {human(t.total_received)} received"
                )
        elif cmd in ("send", "text"):
            s = self.target_session()
            if not s:
                return self.say("No phone is connected.")
            try:
                if cmd == "text":
                    self.svc.core.outbox.add_text(s.session_id, arg)
                    self.say("Queued text for the phone.")
                else:
                    p = Path(arg.strip().strip('"')).expanduser()
                    if p.is_dir():
                        n = len(self.svc.core.outbox.add_folder(s.session_id, p))
                        self.say(
                            f"Queued {n} files from {p.name} for the phone. It will ask to accept."
                        )
                    else:
                        self.svc.core.outbox.add_file(s.session_id, p)
                        self.say(f"Queued {p.name} for the phone. It will ask to accept.")
            except ApiError as e:
                self.say(f"Can't send: {e.message}")
        else:
            self.say(f"Unknown command '{cmd}'. Type 'help'.")

    # -- main loop -----------------------------------------------------------------------------
    def run(self) -> None:
        def reader():
            for line in sys.stdin:
                self.events.put(("line", line))
            self.events.put(("eof",))

        threading.Thread(target=reader, daemon=True, name="stdin").start()
        while not self.stop.is_set():
            try:
                ev = self.events.get(timeout=0.3)
            except queue.Empty:
                continue
            if ev[0] == "line":
                self.handle_line(ev[1])
            elif ev[0] == "eof":
                self.stop.set()
            else:
                self.handle_event(ev)


def pick_host(requested: str | None) -> str:
    addrs = local_ipv4_addresses()
    if requested:
        return requested
    if not addrs:
        raise SystemExit("No network connection found. Join Wi-Fi or your phone's hotspot first.")
    if len(addrs) > 1:
        print("Network interfaces:", file=sys.stderr)
        for i, (n, ip) in enumerate(addrs):
            print(f"  [{i}] {ip}  ({n})", file=sys.stderr)
        raw = input("Listen on which? [0] ").strip() or "0"
        return addrs[int(raw)][1]
    return addrs[0][1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="localbridge --cli", description="LocalBridge terminal receiver"
    )
    ap.add_argument("--host", help="IPv4 address of the interface to listen on")
    ap.add_argument("--dest", help="folder to save received files in")
    ap.add_argument("--name", help="name shown on the phone")
    ap.add_argument("--no-advertise", action="store_true", help="don't announce via mDNS")
    args = ap.parse_args(argv)
    st = Settings.load()
    host = pick_host(args.host)
    dest = Path(args.dest or st.destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    svc = ReceiverService(
        ReceiverConfig(
            host,
            0,
            dest,
            args.name or st.device_name,
            advertise=st.advertise and not args.no_advertise,
        )
    )
    cli = Cli(svc)
    cli.attach()
    try:
        svc.start()
    except (OSError, RuntimeError) as e:
        print(f"Could not start: {e}", file=sys.stderr)
        return 1
    try:
        cli.banner()
        cli.run()
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            svc.stop()
        print("Receiver stopped. Nothing is listening.", flush=True)
    return 0

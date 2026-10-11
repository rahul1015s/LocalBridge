"""Cross-platform (Windows/macOS/Linux) Tk window for the receiver.

Same security behaviour as the GTK app: explicit pairing and transfer approval, expiring QR,
nothing saved until accepted. Tk has no built-in drag-and-drop, so files are chosen with buttons.
"""

from __future__ import annotations

import base64
import queue
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .cli import human, qr_ascii  # noqa: F401  (human reused for sizes)
from .models import ApiError, TransferState
from .network import local_ipv4_addresses
from .server import ReceiverConfig, ReceiverService
from .settings import Settings

TEAL = "#0b7a70"


def qr_png_b64(data: str) -> str:
    import io

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=5, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class TkApp:
    def __init__(self, root: tk.Tk, settings: Settings | None = None):
        self.root = root
        self.settings = settings or Settings.load()
        self.svc: ReceiverService | None = None
        self.ifaces: list[tuple[str, str]] = []
        self._sessions: list = []
        self.events: queue.Queue = queue.Queue()
        self.prompted: set[str] = set()
        self.qr_deadline = 0.0
        self._qr_image: tk.PhotoImage | None = None
        # Injectable so tests can answer without a human.
        self.ask_yes_no = lambda title, text: messagebox.askyesno(title, text, default="no")
        root.title("LocalBridge")
        root.geometry("620x860")
        self._build()
        self.refresh_interfaces()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self.tick)

    @property
    def running(self) -> bool:
        return bool(self.svc and self.svc.running)

    # -- layout ------------------------------------------------------------------------
    def _build(self) -> None:
        r = self.root
        pad = {"padx": 12, "pady": 4}
        head = tk.Frame(r)
        head.pack(fill="x", **pad)
        self.status = tk.Label(
            head, text="Receiver stopped", font=("TkDefaultFont", 16, "bold"), anchor="w"
        )
        self.status.pack(fill="x")
        self.sub = tk.Label(head, text="Not listening on any network.", anchor="w", fg="#666")
        self.sub.pack(fill="x")
        self.start_btn = tk.Button(
            head, text="Start receiving", command=self.toggle, bg=TEAL, fg="white"
        )
        self.start_btn.pack(anchor="w", pady=6)

        self.qr_frame = tk.Frame(r, bd=1, relief="groove")
        self.qr_label = tk.Label(self.qr_frame)
        self.qr_label.pack(pady=6)
        self.qr_info = tk.Label(self.qr_frame, fg="#555", justify="center")
        self.qr_info.pack()
        row = tk.Frame(self.qr_frame)
        row.pack(pady=4)
        tk.Button(row, text="Copy pairing link", command=self.copy_link).pack(side="left", padx=4)
        self.new_btn = tk.Button(row, text="New pairing code", command=self.new_code)
        self.new_btn.pack(side="left", padx=4)

        cfg = ttk.LabelFrame(r, text="Receiver")
        cfg.pack(fill="x", **pad)
        ttk.Label(cfg, text="Computer name").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.name_var = tk.StringVar(value=self.settings.device_name)
        self.name_entry = ttk.Entry(cfg, textvariable=self.name_var, width=30)
        self.name_entry.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(cfg, text="Network").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.iface_var = tk.StringVar()
        self.iface = ttk.Combobox(cfg, textvariable=self.iface_var, state="readonly", width=28)
        self.iface.grid(row=1, column=1, sticky="we", padx=6)
        ttk.Button(cfg, text="Rescan", command=self.refresh_interfaces).grid(
            row=1, column=2, padx=4
        )
        ttk.Label(cfg, text="Save files to").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.dest_var = tk.StringVar(value=self.settings.destination)
        ttk.Entry(cfg, textvariable=self.dest_var, state="readonly").grid(
            row=2, column=1, sticky="we", padx=6
        )
        bf = ttk.Frame(cfg)
        bf.grid(row=2, column=2)
        self.choose_btn = ttk.Button(bf, text="Choose…", command=self.choose_dest)
        self.choose_btn.pack(side="left")
        ttk.Button(bf, text="Open", command=self.open_folder).pack(side="left")
        self.adv_var = tk.BooleanVar(value=self.settings.advertise)
        self.adv_check = ttk.Checkbutton(
            cfg, text="Let nearby phones find this computer (random ID only)", variable=self.adv_var
        )
        self.adv_check.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=3)
        cfg.columnconfigure(1, weight=1)

        self.dev_frame = ttk.LabelFrame(r, text="Connected phones")
        self.dev_list = tk.Listbox(self.dev_frame, height=2)
        self.dev_list.pack(fill="x", side="left", expand=True, padx=6, pady=4)
        ttk.Button(self.dev_frame, text="Disconnect", command=self.disconnect).pack(
            side="right", padx=6
        )

        self.out_frame = ttk.LabelFrame(r, text="Send to phone")
        b = ttk.Frame(self.out_frame)
        b.pack(fill="x", padx=6, pady=4)
        ttk.Button(b, text="Files…", command=self.pick_files).pack(side="left")
        ttk.Button(b, text="Folder…", command=self.pick_folder).pack(side="left", padx=4)
        self.text_var = tk.StringVar()
        te = ttk.Entry(b, textvariable=self.text_var)
        te.pack(side="left", fill="x", expand=True, padx=4)
        te.bind("<Return>", lambda _e: self.send_text())
        ttk.Button(b, text="Send text", command=self.send_text).pack(side="left")
        self.out_tree = ttk.Treeview(self.out_frame, columns=("state",), height=3)
        self.out_tree.heading("#0", text="Item")
        self.out_tree.heading("state", text="Status")
        self.out_tree.pack(fill="x", padx=6, pady=4)

        tr = ttk.LabelFrame(r, text="Transfers")
        tr.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(tr, columns=("state", "progress"), height=6)
        self.tree.heading("#0", text="From / files")
        self.tree.heading("state", text="Status")
        self.tree.heading("progress", text="Progress")
        self.tree.pack(fill="both", expand=True, padx=6, pady=4)
        ttk.Button(tr, text="Cancel selected", command=self.cancel_selected).pack(
            anchor="e", padx=6, pady=2
        )

        self.msg_frame = ttk.LabelFrame(r, text="Text and links from phone (double-click to copy)")
        self.msg_list = tk.Listbox(self.msg_frame, height=3)
        self.msg_list.pack(fill="x", padx=6, pady=4)
        self.msg_list.bind("<Double-Button-1>", self.copy_message)
        self._render()

    # -- state ------------------------------------------------------------------------------
    def _render(self) -> None:
        run = self.running
        for frame in (self.qr_frame, self.dev_frame, self.out_frame, self.msg_frame):
            frame.pack_forget()
        if run:
            self.status.config(text="Receiving")
            host = self.svc.core.cfg.host
            self.sub.config(
                text=f"Listening on {host}:{self.svc.port} (encrypted). Nothing is saved until you approve it."
            )
            self.start_btn.config(text="Stop receiving", bg="#b3261e")
            self.qr_frame.pack(fill="x", padx=12, pady=4, after=self.start_btn.master)
        else:
            self.status.config(text="Receiver stopped")
            self.sub.config(
                text="Not listening on any network. Start receiving to show a pairing code."
            )
            self.start_btn.config(text="Start receiving", bg=TEAL)
        state = "disabled" if run else "normal"
        self.name_entry.config(state=state)
        self.choose_btn.config(state=state)
        self.adv_check.config(state=state)
        self.iface.config(state="disabled" if run else "readonly")
        self.start_btn.config(state="normal" if self.ifaces else "disabled")

    def refresh_interfaces(self) -> None:
        self.ifaces = local_ipv4_addresses()
        labels = [f"{ip}  ({n})" for n, ip in self.ifaces] or ["No network connection"]
        self.iface.config(values=labels)
        sel = next((i for i, (n, _) in enumerate(self.ifaces) if n == self.settings.interface), 0)
        self.iface.current(sel)
        if not self.running:
            self._render()

    def choose_dest(self) -> None:
        d = filedialog.askdirectory(title="Choose where received files are saved")
        if d:
            self.settings.destination = d
            self.dest_var.set(d)
            self.settings.save()

    def open_folder(self) -> None:
        import os
        import subprocess
        import sys

        d = Path(self.settings.destination).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(d)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(d)])  # noqa: S603, S607

    # -- start / stop ------------------------------------------------------------------------
    def toggle(self) -> None:
        self.stop() if self.running else self.start()

    def start(self) -> None:
        if not self.ifaces:
            return
        name, ip = self.ifaces[max(0, self.iface.current())]
        self.settings.device_name = self.name_var.get().strip()[:64] or self.settings.device_name
        self.settings.advertise = bool(self.adv_var.get())
        dest = Path(self.settings.destination).expanduser()
        try:
            dest.mkdir(parents=True, exist_ok=True)
            svc = ReceiverService(
                ReceiverConfig(
                    ip, 0, dest, self.settings.device_name, advertise=self.settings.advertise
                )
            )
            c = svc.core
            c.pairing.subscribe(lambda e, p: self.events.put(("pair", e, p)))
            c.transfers.subscribe(lambda t: self.events.put(("transfer", t)))
            c.subscribe_messages(lambda m: self.events.put(("msg", m)))
            c.outbox.subscribe(lambda i: self.events.put(("outbox", i)))
            svc.start()
        except (OSError, RuntimeError, ApiError) as e:
            messagebox.showerror("Could not start", str(e))
            return
        self.svc = svc
        self.settings.interface = name
        self.settings.save()
        self.new_code()
        self._render()

    def stop(self) -> None:
        if self.svc:
            self.svc.stop()
            self.svc = None
        self.prompted.clear()
        for t in (self.tree, self.out_tree):
            t.delete(*t.get_children())
        self.msg_list.delete(0, "end")
        self._render()

    def on_close(self) -> None:
        self.stop()
        self.settings.save()
        self.root.destroy()

    # -- pairing code --------------------------------------------------------------------------
    def new_code(self) -> None:
        if not self.svc:
            return
        self.svc.core.pairing.new_offer()
        self.qr_deadline = time.monotonic() + self.svc.core.limits.pairing_ttl_seconds
        self._qr_image = tk.PhotoImage(data=qr_png_b64(self.svc.pairing_uri()))
        self.qr_label.config(image=self._qr_image)
        self.new_btn.pack_forget()

    def copy_link(self) -> None:
        if self.svc and self.svc.core.pairing.current_offer():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.svc.pairing_uri())

    # -- events (server thread -> queue -> Tk thread) -----------------------------------------------
    def tick(self) -> None:
        try:
            while True:
                self.handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.running:
            left = int(self.qr_deadline - time.monotonic())
            if self.svc.core.pairing.current_offer() and left > 0:
                self.qr_info.config(
                    text=f"On your phone: LocalBridge → Connect to computer → scan.\nCode expires in {left // 60}:{left % 60:02d}"
                )
            else:
                self.qr_label.config(image="")
                self._qr_image = None
                self.qr_info.config(text="Pairing code expired or already used.")
                self.new_btn.pack(side="left", padx=4)
            self.refresh_devices()
        self.root.after(200, self.tick)

    def handle(self, ev) -> None:
        if not self.svc:
            return
        kind = ev[0]
        if kind == "pair" and ev[1] == "pairing_requested":
            p = ev[2]
            yes = self.ask_yes_no(
                "Pair with this phone?",
                f"“{p.device_name}” at {p.client_ip} scanned your code.\nOnly continue if this is your phone.",
            )
            self.svc.core.pairing.decide(p.pairing_id, bool(yes))
        elif kind == "transfer":
            self.on_transfer(ev[1])
        elif kind == "msg":
            self.msg_frame.pack(fill="x", padx=12, pady=4)
            self.msg_list.insert(0, ev[1].text.replace("\n", " ")[:200])
            self.msg_list.texts = [ev[1].text, *getattr(self.msg_list, "texts", [])]
        elif kind == "outbox":
            self.refresh_outbox()

    def on_transfer(self, t) -> None:
        self.refresh_transfer(t)
        if t.state == TransferState.AWAITING_APPROVAL and t.transfer_id not in self.prompted:
            self.prompted.add(t.transfer_id)
            folders: dict[str, int] = {}
            loose = []
            for f in t.files:
                if f.dirs:
                    folders[f.dirs[0]] = folders.get(f.dirs[0], 0) + 1
                else:
                    loose.append(f)
            lines = [f"[folder] {k}/ ({n} files)" for k, n in list(folders.items())[:6]]
            lines += [f"{f.name}  ({human(f.declared_size)})" for f in loose[:8]]
            total = human(t.total_declared) if t.size_known else "size not declared"
            yes = self.ask_yes_no(
                f"Receive {len(t.files)} file(s)?",
                f"From “{t.device_name}” (paired this session)\nTotal: {total}\nSave to: {self.settings.destination}\n\n"
                + "\n".join(lines),
            )
            try:
                (self.svc.core.transfers.approve if yes else self.svc.core.transfers.reject)(
                    t.transfer_id
                )
            except ApiError:
                messagebox.showinfo("LocalBridge", "That request is no longer active.")

    def refresh_transfer(self, t) -> None:
        iid = t.transfer_id
        title = (
            t.files[0].name if len(t.files) == 1 else f"{len(t.files)} files from {t.device_name}"
        )
        got, tot = t.total_received, t.total_declared
        prog = f"{100 * got // tot}%" if t.size_known and tot else (human(got) if got else "")
        vals = (t.state.value.title(), prog)
        if self.tree.exists(iid):
            self.tree.item(iid, text=title, values=vals)
        else:
            self.tree.insert("", 0, iid=iid, text=title, values=vals)

    def cancel_selected(self) -> None:
        for iid in self.tree.selection():
            if self.svc:
                self.svc.core.transfers.cancel(iid, None, "Cancelled by receiver.")

    def refresh_devices(self) -> None:
        sessions = self.svc.core.pairing.sessions()
        self._sessions = sessions
        self.dev_list.delete(0, "end")
        for s in sessions:
            self.dev_list.insert("end", f"{s.device_name} — paired for this session only")
        if sessions:
            self.dev_frame.pack(fill="x", padx=12, pady=4, after=self.qr_frame)
            self.out_frame.pack(fill="x", padx=12, pady=4, after=self.dev_frame)
        else:
            self.dev_frame.pack_forget()
            self.out_frame.pack_forget()

    def disconnect(self) -> None:
        sel = self.dev_list.curselection()
        if self.svc and sel and sel[0] < len(self._sessions):
            self.svc.core.pairing.revoke_session(self._sessions[sel[0]].session_id)

    # -- outbox ------------------------------------------------------------------------------------
    def _target(self):
        s = self.svc.core.pairing.sessions() if self.running else []
        return s[-1] if s else None

    def pick_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Choose files to send to your phone")
        self.queue_paths(list(paths))

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="Choose a folder to send to your phone")
        if d:
            self.queue_paths([d])

    def queue_paths(self, paths: list[str]) -> None:
        s = self._target()
        if not s:
            return messagebox.showinfo("LocalBridge", "No phone is connected.")
        for p in paths:
            try:
                (
                    self.svc.core.outbox.add_folder
                    if Path(p).is_dir()
                    else self.svc.core.outbox.add_file
                )(s.session_id, Path(p))
            except ApiError as e:
                messagebox.showwarning("LocalBridge", e.message)
        self.refresh_outbox()

    def send_text(self) -> None:
        s, text = self._target(), self.text_var.get().strip()
        if s and text:
            try:
                self.svc.core.outbox.add_text(s.session_id, text)
                self.text_var.set("")
            except ApiError as e:
                messagebox.showwarning("LocalBridge", e.message)
            self.refresh_outbox()

    def refresh_outbox(self) -> None:
        if not self.running:
            return
        self.out_tree.delete(*self.out_tree.get_children())
        labels = {
            "PREPARING": "Preparing…",
            "READY": "Waiting for the phone to accept",
            "DELIVERED": "Saved on phone",
            "DECLINED": "Declined",
            "FAILED": "Failed",
        }
        seen: set[str] = set()
        for it in self.svc.core.outbox.items():
            if it.group:
                if it.group in seen:
                    continue
                seen.add(it.group)
                n = sum(1 for x in self.svc.core.outbox.items() if x.group == it.group)
                self.out_tree.insert(
                    "",
                    "end",
                    text=f"Folder “{(it.dir or '').split('/')[0]}” — {n} files",
                    values=(labels.get(it.state, it.state),),
                )
            else:
                self.out_tree.insert(
                    "",
                    "end",
                    text=it.name if it.kind == "file" else (it.text or "")[:50],
                    values=(labels.get(it.state, it.state),),
                )

    def copy_message(self, _e=None) -> None:
        sel = self.msg_list.curselection()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.msg_list.texts[sel[0]])


def main() -> int:
    root = tk.Tk()
    TkApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

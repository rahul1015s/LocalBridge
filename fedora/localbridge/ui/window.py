"""Main LocalBridge window: receiver control, pairing QR, approvals and transfers."""

from __future__ import annotations

import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..models import TERMINAL_STATES, TransferState  # noqa: E402
from ..network import local_ipv4_addresses  # noqa: E402
from ..server import ReceiverConfig, ReceiverService  # noqa: E402
from ..settings import Settings  # noqa: E402
from ..transfers import Transfer  # noqa: E402
from .qr import qr_texture  # noqa: E402


def human_size(n: int | None) -> str:
    if n is None:
        return "unknown size"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1000 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1000
    return f"{n} B"


def human_rate(bps: float) -> str:
    return f"{human_size(int(bps))}/s"


class TransferRow(Gtk.ListBoxRow):
    def __init__(self, t: Transfer):
        super().__init__(activatable=False, selectable=False)
        self.tid = t.transfer_id
        self._started = time.monotonic()
        self._last = (self._started, 0)
        self._rate = 0.0
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12
        )
        box.set_margin_start(12)
        box.set_margin_end(12)
        top = Gtk.Box(spacing=8)
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.title.add_css_class("heading")
        self.pill = Gtk.Label()
        self.pill.add_css_class("lb-pill")
        self.cancel = Gtk.Button(icon_name="process-stop-symbolic", tooltip_text="Cancel transfer")
        self.cancel.add_css_class("flat")
        self.cancel.update_property([Gtk.AccessibleProperty.LABEL], ["Cancel transfer"])
        top.append(self.title)
        top.append(self.pill)
        top.append(self.cancel)
        self.bar = Gtk.ProgressBar()
        self.detail = Gtk.Label(xalign=0, wrap=True)
        self.detail.add_css_class("dim-label")
        self.detail.add_css_class("caption")
        box.append(top)
        box.append(self.bar)
        box.append(self.detail)
        self.set_child(box)

    def update(self, t: Transfer) -> None:
        names = [f.name for f in t.files]
        self.title.set_text(
            names[0] if len(names) == 1 else f"{len(names)} files from {t.device_name}"
        )
        total, got = t.total_declared, t.total_received
        now = time.monotonic()
        if now - self._last[0] >= 0.5 and t.state == TransferState.TRANSFERRING:
            self._rate = (got - self._last[1]) / (now - self._last[0])
            self._last = (now, got)
        for c in ("lb-pill-ok", "lb-pill-wait", "lb-pill-bad"):
            self.pill.remove_css_class(c)
        label, css = {
            TransferState.COMPLETED: ("Done", "lb-pill-ok"),
            TransferState.TRANSFERRING: ("Receiving", "lb-pill-wait"),
            TransferState.VERIFYING: ("Verifying", "lb-pill-wait"),
            TransferState.AWAITING_APPROVAL: ("Waiting for you", "lb-pill-wait"),
            TransferState.APPROVED: ("Approved", "lb-pill-wait"),
        }.get(t.state, (t.state.value.title(), "lb-pill-bad"))
        self.pill.set_text(label)
        self.pill.add_css_class(css)
        active = t.state not in TERMINAL_STATES
        self.cancel.set_visible(active)
        self.bar.set_visible(t.state in (TransferState.TRANSFERRING, TransferState.VERIFYING))
        if t.size_known and total > 0:
            self.bar.set_fraction(min(1.0, got / total))
        else:
            self.bar.pulse()  # honest: size unknown, so no fake percentage
        if t.state == TransferState.TRANSFERRING:
            eta = ""
            if self._rate > 0 and t.size_known and total > got:
                eta = f" · about {int((total - got) / self._rate)} s left"
            self.detail.set_text(
                f"{human_size(got)} of {human_size(total) if t.size_known else '?'}"
                f" · {human_rate(self._rate)}{eta}"
            )
        elif t.state == TransferState.COMPLETED:
            self.detail.set_text(f"Saved {len(names)} file(s) · SHA-256 verified")
        else:
            self.detail.set_text(t.reason or "")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, settings: Settings):
        super().__init__(
            application=app, title="LocalBridge", default_width=560, default_height=860
        )
        self.settings = settings
        self.service: ReceiverService | None = None
        self._rows: dict[str, TransferRow] = {}
        self._prompted: set[str] = set()
        self._qr_deadline = 0.0
        self._last_ui_push = 0.0
        self._build()
        self._refresh_interfaces()
        GLib.timeout_add(500, self._tick)
        self.connect("close-request", self._on_close)

    # -- layout -----------------------------------------------------------------------
    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        about = Gio.Menu()
        about.append("About LocalBridge", "app.about")
        header.pack_end(
            Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=about, primary=True)
        )
        tv.add_top_bar(header)

        page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=24
        )
        page.set_margin_start(16)
        page.set_margin_end(16)

        # status card
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("card")
        card.add_css_class("lb-card")
        self.status_title = Gtk.Label(xalign=0, wrap=True)
        self.status_title.add_css_class("lb-status-title")
        self.status_sub = Gtk.Label(xalign=0, wrap=True)
        self.status_sub.add_css_class("dim-label")
        self.start_btn = Gtk.Button(halign=Gtk.Align.START)
        self.start_btn.add_css_class("pill")
        self.start_btn.connect("clicked", self._toggle)
        card.append(self.status_title)
        card.append(self.status_sub)
        card.append(self.start_btn)
        page.append(card)

        # pairing card
        self.pair_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.FILL
        )
        self.pair_card.add_css_class("card")
        self.pair_card.add_css_class("lb-card")
        self.qr_pic = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN,
            can_shrink=True,
            halign=Gtk.Align.CENTER,
            hexpand=False,
            vexpand=False,
        )
        self.qr_pic.set_size_request(240, 240)
        self.qr_pic.add_css_class("lb-qr")
        self.qr_pic.update_property([Gtk.AccessibleProperty.LABEL], ["Pairing QR code"])
        self.qr_info = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.qr_info.add_css_class("dim-label")
        self.fp_label = Gtk.Label(wrap=True, selectable=True)
        self.fp_label.add_css_class("lb-mono")
        self.fp_label.add_css_class("caption")
        self.new_code_btn = Gtk.Button(label="New pairing code", halign=Gtk.Align.CENTER)
        self.new_code_btn.connect("clicked", lambda *_: self._new_code())
        self.copy_btn = Gtk.Button(label="Copy pairing link", halign=Gtk.Align.CENTER)
        self.copy_btn.add_css_class("flat")
        self.copy_btn.connect("clicked", self._copy_link)
        for w in (self.qr_pic, self.qr_info, self.fp_label, self.copy_btn, self.new_code_btn):
            self.pair_card.append(w)
        page.append(self.pair_card)

        # settings
        grp = Adw.PreferencesGroup(title="Receiver")
        self.name_row = Adw.EntryRow(title="This computer's name")
        self.name_row.set_text(self.settings.device_name)
        self.name_row.connect("changed", self._name_changed)
        self.iface_row = Adw.ComboRow(
            title="Network interface", subtitle="Only this address will listen"
        )
        refresh = Gtk.Button(
            icon_name="view-refresh-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Rescan networks",
        )
        refresh.add_css_class("flat")
        refresh.update_property([Gtk.AccessibleProperty.LABEL], ["Rescan networks"])
        refresh.connect("clicked", lambda *_: self._refresh_interfaces())
        self.iface_row.add_suffix(refresh)
        self.dest_row = Adw.ActionRow(title="Save files to")
        pick = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        pick.connect("clicked", self._choose_dest)
        self.dest_row.add_suffix(pick)
        self.pick_btn = pick
        for r in (self.name_row, self.iface_row, self.dest_row):
            grp.add(r)
        page.append(grp)
        self._update_dest_row()

        # devices
        self.dev_group = Adw.PreferencesGroup(title="Connected phones", visible=False)
        self.dev_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.dev_list.add_css_class("boxed-list")
        self.dev_group.add(self.dev_list)
        page.append(self.dev_group)

        # transfers
        self.tr_group = Adw.PreferencesGroup(title="Transfers")
        self.tr_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.tr_list.add_css_class("boxed-list")
        self.empty = Adw.ActionRow(
            title="No transfers yet", subtitle="Files sent from your phone will appear here."
        )
        self.tr_list.append(self.empty)
        self.tr_group.add(self.tr_list)
        page.append(self.tr_group)

        clamp = Adw.Clamp(maximum_size=640, child=page)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True
        )
        tv.set_content(scroller)
        self.toasts.set_child(tv)
        self.set_content(self.toasts)
        self._render_state()

    # -- state rendering ----------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self.service and self.service.running)

    def _render_state(self) -> None:
        run = self.running
        self.pair_card.set_visible(run)
        self.iface_row.set_sensitive(not run)
        self.pick_btn.set_sensitive(not run)
        self.name_row.set_sensitive(not run)
        for c in ("suggested-action", "destructive-action"):
            self.start_btn.remove_css_class(c)
        if run:
            svc = self.service
            self.status_title.set_text("Receiving")
            self.status_sub.set_text(
                f"Listening on {svc.core.cfg.host}:{svc.port} (encrypted). Nothing is saved until you approve it."
            )
            self.start_btn.set_label("Stop receiving")
            self.start_btn.add_css_class("destructive-action")
        else:
            self.status_title.set_text("Receiver stopped")
            self.status_sub.set_text(
                "Not listening on any network. Start receiving to show a pairing code."
            )
            self.start_btn.set_label("Start receiving")
            self.start_btn.add_css_class("suggested-action")
            self.dev_group.set_visible(False)
        self.start_btn.set_sensitive(bool(self._ifaces) if hasattr(self, "_ifaces") else False)

    def _refresh_interfaces(self) -> None:
        self._ifaces = local_ipv4_addresses()
        labels = [f"{ip}  ·  {name}" for name, ip in self._ifaces] or ["No network connection"]
        self.iface_row.set_model(Gtk.StringList.new(labels))
        for i, (name, _) in enumerate(self._ifaces):
            if name == self.settings.interface:
                self.iface_row.set_selected(i)
        if not self._ifaces:
            self.iface_row.set_subtitle("Connect to Wi-Fi or your phone's hotspot first")
        else:
            self.iface_row.set_subtitle("Only this address will listen")
        if not self.running:
            self._render_state()

    def _update_dest_row(self) -> None:
        self.dest_row.set_subtitle(self.settings.destination)

    def _name_changed(self, row) -> None:
        self.settings.device_name = row.get_text().strip()[:64] or self.settings.device_name

    def _choose_dest(self, *_):
        dlg = Gtk.FileDialog(title="Choose where received files are saved")
        dlg.select_folder(self, None, self._dest_done)

    def _dest_done(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            self.settings.destination = f.get_path()
            self._update_dest_row()
            self.settings.save()

    # -- start/stop ---------------------------------------------------------------------------------
    def _toggle(self, *_):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        if not self._ifaces:
            return
        name, ip = self._ifaces[self.iface_row.get_selected()]
        dest = Path(self.settings.destination).expanduser()
        try:
            dest.mkdir(parents=True, exist_ok=True)
            cfg = ReceiverConfig(ip, 0, dest, self.settings.device_name)
            svc = ReceiverService(cfg)
            svc.core.pairing.subscribe(lambda e, p: GLib.idle_add(self._on_pair_event, e, p))
            svc.core.transfers.subscribe(lambda t: self._on_transfer_event(t))
            svc.start()
        except (OSError, RuntimeError, Exception) as e:  # noqa: BLE001
            self._toast(f"Could not start: {e}")
            return
        self.service = svc
        self.settings.interface = name
        self.settings.save()
        self._new_code()
        self._render_state()

    def _stop(self) -> None:
        if self.service:
            self.service.stop()
            self.service = None
        self._clear_rows_for_stopped()
        self._render_state()
        self._toast("Receiver stopped. Nothing is listening.")

    def _clear_rows_for_stopped(self) -> None:
        self._prompted.clear()

    def _new_code(self) -> None:
        svc = self.service
        if not svc:
            return
        offer = svc.core.pairing.new_offer()
        self._qr_deadline = time.monotonic() + svc.core.limits.pairing_ttl_seconds
        self.qr_pic.set_paintable(qr_texture(svc.pairing_uri()))
        self.qr_pic.set_visible(True)
        fp = svc.identity.fingerprint.upper()
        self.fp_label.set_text(
            "Fingerprint " + " ".join(fp[i : i + 4] for i in range(0, 24, 4)) + " …"
        )
        self.new_code_btn.set_visible(False)
        self.copy_btn.set_visible(True)
        _ = offer

    def _copy_link(self, *_):
        offer = self.service and self.service.core.pairing.current_offer()
        if not offer:
            return
        self.get_clipboard().set(self.service.pairing_uri())
        self._toast("Pairing link copied. It is as private as the QR code.")

    def _tick(self) -> bool:
        if self.running:
            left = int(self._qr_deadline - time.monotonic())
            offer = self.service.core.pairing.current_offer()
            if offer and left > 0:
                self.qr_info.set_text(
                    f"On your phone: LocalBridge → Connect to computer → scan.\nCode expires in {left // 60}:{left % 60:02d}"
                )
            else:
                self.qr_pic.set_visible(False)
                self.copy_btn.set_visible(False)
                self.fp_label.set_text("")
                self.qr_info.set_text("Pairing code expired or already used.")
                self.new_code_btn.set_visible(True)
            self._render_devices()
            for tid, row in self._rows.items():
                for t in self.service.core.transfers.all():
                    if t.transfer_id == tid:
                        row.update(t)
        return GLib.SOURCE_CONTINUE

    def _render_devices(self) -> None:
        sessions = self.service.core.pairing.sessions()
        self.dev_group.set_visible(bool(sessions))
        child = self.dev_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.dev_list.remove(child)
            child = nxt
        for s in sessions:
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(s.device_name),
                subtitle="Paired for this session only",
            )
            b = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER)
            b.connect(
                "clicked",
                lambda _b, sid=s.session_id: self.service.core.pairing.revoke_session(sid),
            )
            row.add_suffix(b)
            self.dev_list.append(row)

    # -- events from the server thread (marshalled to the GTK main loop) -----------------------------------
    def _on_pair_event(self, event, payload):
        if event == "pairing_requested":
            self._ask_pair(payload)
        elif event == "session_created":
            self._toast(f"{payload.device_name} connected")
        return GLib.SOURCE_REMOVE

    def _ask_pair(self, p) -> None:
        d = Adw.AlertDialog(
            heading="Pair with this phone?",
            body=f"“{p.device_name}” at {p.client_ip} scanned your code.\nOnly continue if this is your phone.",
        )
        d.add_response("deny", "Deny")
        d.add_response("pair", "Pair")
        d.set_response_appearance("pair", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("deny")
        d.set_close_response("deny")
        d.connect(
            "response",
            lambda _d, r: (
                self.service and self.service.core.pairing.decide(p.pairing_id, r == "pair")
            ),
        )
        d.present(self)

    def _on_transfer_event(self, t: Transfer) -> None:
        now = time.monotonic()
        terminalish = t.state != TransferState.TRANSFERRING
        if not terminalish and now - self._last_ui_push < 0.2:
            return  # throttle progress-only updates
        self._last_ui_push = now
        GLib.idle_add(self._apply_transfer, t)

    def _apply_transfer(self, t: Transfer):
        if self.empty.get_parent():
            self.tr_list.remove(self.empty)
        row = self._rows.get(t.transfer_id)
        if row is None:
            row = TransferRow(t)
            row.cancel.connect(
                "clicked",
                lambda *_: (
                    self.service
                    and self.service.core.transfers.cancel(
                        t.transfer_id, None, "Cancelled by receiver."
                    )
                ),
            )
            self._rows[t.transfer_id] = row
            self.tr_list.prepend(row)
        row.update(t)
        if t.state == TransferState.AWAITING_APPROVAL and t.transfer_id not in self._prompted:
            self._prompted.add(t.transfer_id)
            self._ask_approve(t)
        return GLib.SOURCE_REMOVE

    def _ask_approve(self, t: Transfer) -> None:
        shown = [f"• {f.name}  ({human_size(f.declared_size)})" for f in t.files[:8]]
        if len(t.files) > 8:
            shown.append(f"…and {len(t.files) - 8} more")
        total = human_size(t.total_declared) if t.size_known else "size not declared"
        dest = self.settings.destination
        d = Adw.AlertDialog(
            heading=f"Receive {len(t.files)} file{'s' if len(t.files) != 1 else ''}?",
            body=(
                f"From “{t.device_name}” (paired this session)\nTotal: {total}\nSave to: {dest}\n\n"
                + "\n".join(shown)
            ),
        )
        d.add_response("reject", "Reject")
        d.add_response("accept", "Accept")
        d.set_response_appearance("accept", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("reject")
        d.set_close_response("reject")

        def done(_d, r):
            if not self.service:
                return
            try:
                if r == "accept":
                    self.service.core.transfers.approve(t.transfer_id)
                else:
                    self.service.core.transfers.reject(t.transfer_id)
            except Exception:  # noqa: BLE001 - already expired/cancelled by the sender
                self._toast("That request is no longer active.")

        d.connect("response", done)
        d.present(self)

    # -- misc ---------------------------------------------------------------------------------------------------
    def _toast(self, msg: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=4))

    def _on_close(self, *_):
        if self.service:
            self.service.stop()
            self.service = None
        self.settings.save()
        return False

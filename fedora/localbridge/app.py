"""LocalBridge desktop entry point (GTK4 + Libadwaita)."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from . import __version__  # noqa: E402
from .settings import Settings  # noqa: E402
from .ui import style  # noqa: E402
from .ui.window import MainWindow  # noqa: E402


class LocalBridgeApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.localbridge.LocalBridge",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.settings = Settings.load()

    def do_startup(self):
        Adw.Application.do_startup(self)
        style.install()
        act = Gio.SimpleAction.new("about", None)
        act.connect("activate", self._about)
        self.add_action(act)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        win = self.props.active_window or MainWindow(self, self.settings)
        win.present()

    def _about(self, *_):
        Adw.AboutDialog(
            application_name="LocalBridge",
            application_icon="network-wireless-symbolic",
            version=__version__,
            comments="Private file transfer between your phone and this computer. No cloud, no account.",
            license_type=__import__("gi.repository.Gtk", fromlist=["License"]).License.GPL_3_0,
        ).present(self.props.active_window)


def main() -> int:
    return LocalBridgeApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

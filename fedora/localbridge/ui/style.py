"""Teal accent over the stock Libadwaita palette; follows the system light/dark preference."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gtk

_BASE = """
.lb-card { padding: 20px; border-radius: 16px; }
.lb-status-title { font-size: 20pt; font-weight: 800; }
.lb-qr { background: white; border-radius: 12px; padding: 10px; }
.lb-mono { font-family: monospace; }
.lb-pill { border-radius: 99px; padding: 2px 10px; font-size: 9pt; font-weight: 700; }
.lb-pill-ok { background: alpha(@success_color, .18); color: @success_color; }
.lb-pill-wait { background: alpha(@warning_color, .22); color: @warning_color; }
.lb-pill-bad { background: alpha(@error_color, .18); color: @error_color; }
progressbar > trough, progressbar > trough > progress { min-height: 8px; border-radius: 99px; }
"""
_LIGHT = "@define-color accent_bg_color #0b7a70; @define-color accent_color #0a6b62;"
_DARK = "@define-color accent_bg_color #14a89a; @define-color accent_color #4fd1c5;"


def install() -> None:
    display = Gdk.Display.get_default()
    base = Gtk.CssProvider()
    base.load_from_string(_BASE)
    Gtk.StyleContext.add_provider_for_display(
        display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    accent = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(
        display, accent, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    )
    mgr = Adw.StyleManager.get_default()

    def apply(*_):
        accent.load_from_string(_DARK if mgr.get_dark() else _LIGHT)

    mgr.connect("notify::dark", apply)
    apply()

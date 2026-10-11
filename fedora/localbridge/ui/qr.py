"""Render the pairing URI as a Gdk.Texture (ECC level M, generous quiet zone)."""

from __future__ import annotations

import io

import qrcode
from gi.repository import Gdk, GLib
from qrcode.constants import ERROR_CORRECT_M


def qr_png(data: str, box_size: int = 8) -> bytes:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


def qr_texture(data: str) -> Gdk.Texture:
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(qr_png(data)))

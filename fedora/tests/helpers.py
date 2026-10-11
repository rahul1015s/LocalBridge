"""Pinned-TLS test client: mirrors the fingerprint pinning the Android app performs."""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
from urllib.parse import parse_qs, urlparse


class PinMismatch(Exception):
    pass


class PinnedClient:
    def __init__(self, host: str, port: int, fingerprint: str, token: str | None = None):
        self.host, self.port, self.fp, self.token = host, port, fingerprint, token

    def request(
        self, method, path, *, json_body=None, body=None, headers=None, token="__default__"
    ):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # replaced by the explicit fingerprint check below
        conn = http.client.HTTPSConnection(self.host, self.port, context=ctx, timeout=20)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if hashlib.sha256(der).hexdigest() != self.fp:
                raise PinMismatch("certificate fingerprint mismatch")
            h = dict(headers or {})
            tok = self.token if token == "__default__" else token
            if tok:
                h["Authorization"] = f"Bearer {tok}"
            if json_body is not None:
                body = json.dumps(json_body).encode()
                h["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            raw = r.read()
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                data = raw
            return r.status, data
        finally:
            conn.close()


def parse_pairing_uri(uri: str) -> dict[str, str]:
    u = urlparse(uri)
    assert u.scheme == "localbridge" and u.netloc == "pair"
    return {k: v[0] for k, v in parse_qs(u.query).items()}

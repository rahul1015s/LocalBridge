"""Ephemeral self-signed TLS identity for a receiving session.

Trust is *not* derived from the certificate chain: the sender pins the SHA-256
fingerprint delivered through the QR code, so a self-signed certificate is
only accepted when it matches that fingerprint.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def fingerprint_der(der: bytes) -> str:
    """Lower-case hex SHA-256 of the DER-encoded certificate."""
    return hashlib.sha256(der).hexdigest()


@dataclass
class TlsIdentity:
    cert_path: Path
    key_path: Path
    fingerprint: str
    _tmpdir: tempfile.TemporaryDirectory

    def cleanup(self) -> None:
        self._tmpdir.cleanup()


def generate_identity(host: str, validity_days: int = 1) -> TlsIdentity:
    """Create a P-256 key + self-signed cert valid for ``host`` (an IP literal)."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LocalBridge Receiver")])
    san: list[x509.GeneralName] = [x509.DNSName("localbridge.local")]
    try:
        san.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        san.append(x509.DNSName(host))
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=validity_days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    tmp = tempfile.TemporaryDirectory(prefix="localbridge-tls-")
    os.chmod(tmp.name, 0o700)
    cert_path = Path(tmp.name) / "cert.pem"
    key_path = Path(tmp.name) / "key.pem"
    key_fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(key_fd, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    fp = fingerprint_der(cert.public_bytes(serialization.Encoding.DER))
    return TlsIdentity(cert_path, key_path, fp, tmp)

import ipaddress
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from bizviz.config import KC_CA_KEY, AppConfig
from bizviz.platform.base import SecretStore

log = logging.getLogger(__name__)

CA_VALIDITY = timedelta(days=3650)
LEAF_VALIDITY = timedelta(days=365)
BACKDATE = timedelta(days=1)


def ca_path(cfg: AppConfig) -> Path:
    return cfg.certs_dir / "ca.pem"


def leaf_path(cfg: AppConfig) -> Path:
    return cfg.certs_dir / "leaf.pem"


def leaf_key_path(cfg: AppConfig) -> Path:
    return cfg.certs_dir / "leaf-key.pem"


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    tmp.chmod(mode)
    os.replace(tmp, path)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _load_ca(cfg: AppConfig, secrets: SecretStore) -> x509.Certificate | None:
    path = ca_path(cfg)
    key_pem = secrets.get(KC_CA_KEY)
    if path.exists() and key_pem:
        return x509.load_pem_x509_certificate(path.read_bytes())
    if path.exists() or key_pem:
        log.warning("CA certificate and private key are inconsistent; regenerating both")
    return None


def _generate_ca(cfg: AppConfig, secrets: SecretStore, now: datetime) -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BizViz"),
            x509.NameAttribute(
                NameOID.COMMON_NAME, f"BizViz Local CA ({cfg.hostname or 'localhost'})"
            ),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(now + CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    secrets.set(KC_CA_KEY, _key_pem(key).decode("ascii"))
    _write_atomic(ca_path(cfg), cert.public_bytes(serialization.Encoding.PEM), 0o644)
    return cert


def ensure_ca(cfg: AppConfig, secrets: SecretStore, now: datetime) -> x509.Certificate:
    return _load_ca(cfg, secrets) or _generate_ca(cfg, secrets, now)


def rotate_ca(cfg: AppConfig, secrets: SecretStore, now: datetime) -> x509.Certificate:
    return _generate_ca(cfg, secrets, now)


def leaf_sans(cfg: AppConfig) -> list[x509.GeneralName]:
    names: list[str] = ["localhost"]
    if cfg.hostname:
        names.append(cfg.hostname)
    names.extend(cfg.extra_hostnames)
    names.extend(cfg.previous_hostnames)
    sans: list[x509.GeneralName] = [x509.DNSName(n) for n in dict.fromkeys(names)]
    sans.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    sans.append(x509.IPAddress(ipaddress.IPv6Address("::1")))
    return sans


def issue_leaf(cfg: AppConfig, secrets: SecretStore, now: datetime) -> None:
    ca_cert = ensure_ca(cfg, secrets, now)
    ca_key_pem = secrets.get(KC_CA_KEY)
    assert ca_key_pem is not None
    ca_key = serialization.load_pem_private_key(ca_key_pem.encode("ascii"), password=None)
    assert isinstance(ca_key, ec.EllipticCurvePrivateKey)

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cfg.hostname or "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(now + LEAF_VALIDITY)
        .add_extension(x509.SubjectAlternativeName(leaf_sans(cfg)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_atomic(leaf_key_path(cfg), _key_pem(key), 0o600)
    _write_atomic(leaf_path(cfg), cert.public_bytes(serialization.Encoding.PEM), 0o644)


def _load_cert(path: Path) -> x509.Certificate | None:
    if not path.exists():
        return None
    return x509.load_pem_x509_certificate(path.read_bytes())


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cert_info(cfg: AppConfig) -> dict[str, Any]:
    leaf = _load_cert(leaf_path(cfg))
    ca = _load_cert(ca_path(cfg))
    sans: list[str] = []
    if leaf is not None:
        ext = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        sans = [str(n) for n in ext.get_values_for_type(x509.DNSName)]
        sans += [str(n) for n in ext.get_values_for_type(x509.IPAddress)]
    return {
        "leaf_expires_at": _iso(leaf.not_valid_after_utc) if leaf else None,
        "ca_expires_at": _iso(ca.not_valid_after_utc) if ca else None,
        "leaf_sans": sans,
    }


def leaf_needs_renewal(cfg: AppConfig, now: datetime, days: int = 30) -> bool:
    leaf = _load_cert(leaf_path(cfg))
    if leaf is None or not leaf_key_path(cfg).exists():
        return True
    return leaf.not_valid_after_utc - now <= timedelta(days=days)


def ca_expiring(cfg: AppConfig, now: datetime, days: int = 180) -> bool:
    ca = _load_cert(ca_path(cfg))
    if ca is None:
        return False
    return ca.not_valid_after_utc - now <= timedelta(days=days)


def export_ca_for_airdrop(cfg: AppConfig, dest: Path) -> None:
    shutil.copyfile(ca_path(cfg), dest)

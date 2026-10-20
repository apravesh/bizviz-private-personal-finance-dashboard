import ipaddress
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from bizviz.certs import (
    ca_expiring,
    cert_info,
    ensure_ca,
    export_ca_for_airdrop,
    issue_leaf,
    leaf_needs_renewal,
    leaf_sans,
    rotate_ca,
)
from bizviz.config import KC_CA_KEY, AppConfig, replace_config
from bizviz.platform.fake import FakeSecretStore

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


@pytest.fixture
def host_cfg(cfg: AppConfig) -> AppConfig:
    return replace_config(
        cfg,
        hostname="macbook.local",
        extra_hostnames=("bizviz.example", "macbook.local"),
        previous_hostnames=("old-mac.local",),
    )


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ensure_ca_properties(host_cfg: AppConfig) -> None:
    store = FakeSecretStore(None)
    ca = ensure_ca(host_cfg, store, NOW)
    assert ca == _load(host_cfg.certs_dir / "ca.pem")
    assert ca.subject == ca.issuer
    assert ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == (
        "BizViz Local CA (macbook.local)"
    )
    assert isinstance(ca.public_key(), ec.EllipticCurvePublicKey)
    assert ca.public_key().curve.name == "secp256r1"  # type: ignore[attr-defined]
    assert isinstance(ca.signature_hash_algorithm, hashes.SHA256)
    assert ca.not_valid_before_utc == NOW - timedelta(days=1)
    assert ca.not_valid_after_utc == NOW + timedelta(days=3650)

    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical and bc.value.ca and bc.value.path_length == 0
    ku = ca.extensions.get_extension_for_class(x509.KeyUsage)
    assert ku.critical
    assert ku.value.key_cert_sign and ku.value.crl_sign
    assert not (ku.value.digital_signature or ku.value.key_encipherment or ku.value.key_agreement)
    assert ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)

    pem = store.get(KC_CA_KEY)
    assert pem is not None and "PRIVATE KEY" in pem
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    assert key.public_key().public_numbers() == ca.public_key().public_numbers()  # type: ignore[union-attr]
    for p in host_cfg.certs_dir.iterdir():
        assert b"PRIVATE KEY" not in p.read_bytes()
    assert stat.S_IMODE((host_cfg.certs_dir / "ca.pem").stat().st_mode) == 0o644


def test_ca_cn_falls_back_to_localhost(cfg: AppConfig) -> None:
    ca = ensure_ca(cfg, FakeSecretStore(None), NOW)
    assert ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == (
        "BizViz Local CA (localhost)"
    )


def test_ensure_ca_reuses_existing(cfg: AppConfig) -> None:
    store = FakeSecretStore(None)
    first = ensure_ca(cfg, store, NOW)
    key = store.get(KC_CA_KEY)
    second = ensure_ca(cfg, store, NOW + timedelta(days=5))
    assert second == first
    assert store.get(KC_CA_KEY) == key


@pytest.mark.parametrize("missing", ["file", "key"])
def test_ensure_ca_regenerates_when_half_missing(
    cfg: AppConfig, missing: str, caplog: pytest.LogCaptureFixture
) -> None:
    store = FakeSecretStore(None)
    first = ensure_ca(cfg, store, NOW)
    if missing == "file":
        (cfg.certs_dir / "ca.pem").unlink()
    else:
        store.delete(KC_CA_KEY)
    with caplog.at_level("WARNING"):
        second = ensure_ca(cfg, store, NOW)
    assert second != first
    assert "regenerating" in caplog.text
    assert store.get(KC_CA_KEY) and (cfg.certs_dir / "ca.pem").exists()
    assert _load(cfg.certs_dir / "ca.pem") == second


def test_rotate_ca_replaces_ca(cfg: AppConfig) -> None:
    store = FakeSecretStore(None)
    first = ensure_ca(cfg, store, NOW)
    key = store.get(KC_CA_KEY)
    second = rotate_ca(cfg, store, NOW)
    assert second != first
    assert store.get(KC_CA_KEY) != key
    assert ensure_ca(cfg, store, NOW) == second


def test_leaf_sans_order_and_dedupe(host_cfg: AppConfig) -> None:
    sans = leaf_sans(host_cfg)
    assert sans[:4] == [
        x509.DNSName("localhost"),
        x509.DNSName("macbook.local"),
        x509.DNSName("bizviz.example"),
        x509.DNSName("old-mac.local"),
    ]
    assert sans[4:] == [
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]


def test_leaf_sans_without_hostname(cfg: AppConfig) -> None:
    assert [str(s.value) for s in leaf_sans(cfg)] == ["localhost", "127.0.0.1", "::1"]


def test_issue_leaf_properties(host_cfg: AppConfig) -> None:
    store = FakeSecretStore(None)
    issue_leaf(host_cfg, store, NOW)
    ca = _load(host_cfg.certs_dir / "ca.pem")
    leaf = _load(host_cfg.certs_dir / "leaf.pem")

    assert leaf.issuer == ca.subject
    assert leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "macbook.local"
    assert leaf.not_valid_before_utc == NOW - timedelta(days=1)
    assert leaf.not_valid_after_utc == NOW + timedelta(days=365)
    assert (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days <= 825
    assert isinstance(leaf.signature_hash_algorithm, hashes.SHA256)

    ca_pub = ca.public_key()
    assert isinstance(ca_pub, ec.EllipticCurvePublicKey)
    ca_pub.verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256()))
    leaf.verify_directly_issued_by(ca)

    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == [
        "localhost",
        "macbook.local",
        "bizviz.example",
        "old-mac.local",
    ]
    assert san.get_values_for_type(x509.IPAddress) == [
        ipaddress.IPv4Address("127.0.0.1"),
        ipaddress.IPv6Address("::1"),
    ]
    bc = leaf.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical and not bc.value.ca
    ku = leaf.extensions.get_extension_for_class(x509.KeyUsage)
    assert ku.critical and ku.value.digital_signature
    assert not (ku.value.key_cert_sign or ku.value.crl_sign or ku.value.key_encipherment)
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]
    ski = leaf.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    ca_ski = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    assert ski.digest != aki.key_identifier
    assert aki.key_identifier == ca_ski.digest

    key_file = host_cfg.certs_dir / "leaf-key.pem"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((host_cfg.certs_dir / "leaf.pem").stat().st_mode) == 0o644
    leaf_key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    assert leaf_key.public_key().public_numbers() == leaf.public_key().public_numbers()  # type: ignore[union-attr]
    assert not list(host_cfg.certs_dir.glob("*.tmp"))


def test_issue_leaf_reissue_keeps_ca(cfg: AppConfig) -> None:
    store = FakeSecretStore(None)
    issue_leaf(cfg, store, NOW)
    ca = (cfg.certs_dir / "ca.pem").read_bytes()
    first = (cfg.certs_dir / "leaf.pem").read_bytes()
    issue_leaf(replace_config(cfg, hostname="new.local"), store, NOW + timedelta(days=1))
    assert (cfg.certs_dir / "ca.pem").read_bytes() == ca
    leaf = _load(cfg.certs_dir / "leaf.pem")
    assert leaf.public_bytes(serialization.Encoding.PEM) != first
    leaf.verify_directly_issued_by(_load(cfg.certs_dir / "ca.pem"))


def test_renewal_and_expiry_checks(cfg: AppConfig) -> None:
    assert leaf_needs_renewal(cfg, NOW)
    store = FakeSecretStore(None)
    issue_leaf(cfg, store, NOW)
    assert not leaf_needs_renewal(cfg, NOW + timedelta(days=100))
    assert not leaf_needs_renewal(cfg, NOW + timedelta(days=334))
    assert leaf_needs_renewal(cfg, NOW + timedelta(days=336))
    assert leaf_needs_renewal(cfg, NOW + timedelta(days=400))
    assert not ca_expiring(cfg, NOW)
    assert not ca_expiring(cfg, NOW + timedelta(days=3400))
    assert ca_expiring(cfg, NOW + timedelta(days=3480))
    (cfg.certs_dir / "leaf.pem").unlink()
    assert leaf_needs_renewal(cfg, NOW)


def test_cert_info(host_cfg: AppConfig) -> None:
    assert cert_info(host_cfg) == {
        "leaf_expires_at": None,
        "ca_expires_at": None,
        "leaf_sans": [],
    }
    issue_leaf(host_cfg, FakeSecretStore(None), NOW)
    info = cert_info(host_cfg)
    assert info["leaf_expires_at"] == (NOW + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert info["ca_expires_at"] == (NOW + timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert info["leaf_sans"] == [
        "localhost",
        "macbook.local",
        "bizviz.example",
        "old-mac.local",
        "127.0.0.1",
        "::1",
    ]


def test_export_ca_for_airdrop(cfg: AppConfig, tmp_path: Path) -> None:
    ensure_ca(cfg, FakeSecretStore(None), NOW)
    dest = tmp_path / "BizViz-CA.cer"
    export_ca_for_airdrop(cfg, dest)
    assert dest.read_bytes() == (cfg.certs_dir / "ca.pem").read_bytes()

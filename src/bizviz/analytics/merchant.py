import re

_PREFIX_RE = re.compile(r"^(?:sq|tst|pp|paypal)\s*\*\s*")
_NOISE_RE = re.compile(r"[\d#*]")


def normalize_key(text: str) -> str:
    return " ".join(text.lower().split())


def merchant_key(merchant_name: str | None, name: str | None) -> str:
    if merchant_name and merchant_name.strip():
        return normalize_key(merchant_name)
    text = _PREFIX_RE.sub("", (name or "").lower().strip())
    text = _NOISE_RE.sub("", text)
    return " ".join(text.split()) or "unknown"


def raw_display(merchant_name: str | None, name: str | None) -> str | None:
    return merchant_name or name or None

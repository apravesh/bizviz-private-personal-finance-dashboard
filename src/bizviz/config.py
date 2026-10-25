import dataclasses
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Env = Literal["production", "sandbox"]

KC_DB_KEY = "db-key"
KC_PLAID_CLIENT_ID = "plaid-client-id"
KC_PLAID_SECRET = "plaid-secret"
KC_CA_KEY = "ca-private-key"
KC_TOKEN_PREFIX = "plaid-token:"
KC_ITEM_INDEX = "plaid-item-index"

PORT_PRODUCTION = 8443
PORT_SANDBOX = 8445
PORT_DEMO = 8444
LAUNCH_AGENT_LABEL = "com.bizviz.agent"
SESSION_COOKIE = "bv_session"

# A profile is a separate BizViz on the same Mac (another person, other Plaid connections):
# its own data folder, Keychain entries, background service, login cookie and port.
PROFILE_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")


class InvalidProfileError(ValueError):
    pass


def check_profile(profile: str | None) -> str | None:
    """None (the default profile) or a lowercase name like "wife"; raises on anything else."""
    if profile is None:
        return None
    if not PROFILE_RE.fullmatch(profile) or profile in ("sandbox", "demo"):
        raise InvalidProfileError(
            "A profile name is lowercase letters, digits and dashes, starting with a letter "
            "(at most 32 characters), and not 'sandbox' or 'demo'."
        )
    return profile


@dataclass(frozen=True)
class AppConfig:
    env: Env
    profile: str | None
    home: Path
    db_path: Path
    backups_dir: Path
    certs_dir: Path
    log_dir: Path
    config_toml_path: Path
    keychain_service: str
    port: int
    plaid_env: Env
    hostname: str | None
    extra_hostnames: tuple[str, ...]
    previous_hostnames: tuple[str, ...]
    repo_dir: Path
    web_dist: Path
    launch_agent_label: str
    session_cookie: str
    # Whether the port came from config.toml (or is the default profile's built-in one).
    port_configured: bool


def _suffix(env: Env, profile: str | None) -> str:
    """Name suffix shared by the data folder, logs and Keychain service: "", "-wife", "-sandbox"."""
    return (f"-{profile}" if profile else "") + ("" if env == "production" else "-sandbox")


def _paths(env: Env, profile: str | None) -> tuple[Path, Path]:
    """Return (home, log_dir) for the environment and profile."""
    suffix = _suffix(env, profile)
    override = os.environ.get("BIZVIZ_HOME")
    if override:
        home = Path(f"{override.rstrip('/')}{suffix}")
        return home, home / "logs"
    user_home = Path.home()
    name = f"BizViz{suffix}"
    return (
        user_home / "Library" / "Application Support" / name,
        user_home / "Library" / "Logs" / name,
    )


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value)


def load_config(env: Env, profile: str | None = None) -> AppConfig:
    profile = check_profile(profile)
    home, log_dir = _paths(env, profile)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    for d in (home / "backups", home / "certs", log_dir):
        d.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    toml_path = home / "config.toml"
    if toml_path.exists():
        with toml_path.open("rb") as f:
            data = tomllib.load(f)

    default_port = PORT_PRODUCTION if env == "production" else PORT_SANDBOX
    # A named profile has no built-in port: it must not silently take the default profile's.
    port_configured = "port" in data or profile is None
    hostname = data.get("hostname")
    repo_dir = Path(__file__).resolve().parents[2]
    cookie_suffix = "".join(
        f"_{part}" for part in (profile, None if env == "production" else env) if part
    )
    return AppConfig(
        env=env,
        profile=profile,
        home=home,
        db_path=home / "bizviz.db",
        backups_dir=home / "backups",
        certs_dir=home / "certs",
        log_dir=log_dir,
        config_toml_path=toml_path,
        keychain_service=f"BizViz{_suffix(env, profile)}",
        port=int(data.get("port", default_port)),
        plaid_env=env,
        hostname=str(hostname) if hostname else None,
        extra_hostnames=_str_tuple(data.get("extra_hostnames")),
        previous_hostnames=_str_tuple(data.get("previous_hostnames")),
        repo_dir=repo_dir,
        web_dist=repo_dir / "web" / "dist",
        launch_agent_label=LAUNCH_AGENT_LABEL + (f".{profile}" if profile else ""),
        session_cookie=SESSION_COOKIE + cookie_suffix.replace("-", "_"),
        port_configured=port_configured,
    )


def other_profile_ports(cfg: AppConfig) -> dict[str, int]:
    """Ports of the other BizViz installs on this Mac (data folders next to this one)."""
    ports: dict[str, int] = {}
    for folder in sorted(cfg.home.parent.glob("BizViz*")):
        if folder == cfg.home or not folder.is_dir():
            continue
        port = PORT_SANDBOX if folder.name.endswith("-sandbox") else PORT_PRODUCTION
        toml = folder / "config.toml"
        if toml.exists():
            try:
                with toml.open("rb") as f:
                    port = int(tomllib.load(f).get("port", port))
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                continue
        elif folder.name not in ("BizViz", "BizViz-sandbox"):
            continue
        ports[folder.name] = port
    ports.setdefault("demo", PORT_DEMO)
    return ports


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    escaped = "".join(
        c if ord(c) >= 0x20 and ord(c) != 0x7F else f"\\u{ord(c):04x}" for c in escaped
    )
    return f'"{escaped}"'


def _toml_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def save_config(cfg: AppConfig) -> None:
    lines = [f"port = {cfg.port}", f"plaid_env = {_toml_str(cfg.plaid_env)}"]
    if cfg.hostname is not None:
        lines.append(f"hostname = {_toml_str(cfg.hostname)}")
    lines.append(f"extra_hostnames = {_toml_list(cfg.extra_hostnames)}")
    lines.append(f"previous_hostnames = {_toml_list(cfg.previous_hostnames)}")
    path = cfg.config_toml_path
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    tmp.chmod(0o600)
    os.replace(tmp, path)


def replace_config(cfg: AppConfig, **changes: Any) -> AppConfig:
    return dataclasses.replace(cfg, **changes)

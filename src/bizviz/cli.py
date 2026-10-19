"""The `bizviz` command line: setup/update and restore."""

import argparse
import getpass
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from bizviz import __version__, certs, tokens
from bizviz.auth import revoke_all, set_dashboard_password, validate_password
from bizviz.backup import (
    InvalidPhrase,
    RestoreRefused,
    encode_recovery_phrase,
    make_premigration_hook,
    recover_key,
    restore_backup,
)
from bizviz.config import (
    KC_CA_KEY,
    KC_DB_KEY,
    KC_PLAID_CLIENT_ID,
    KC_PLAID_SECRET,
    AppConfig,
    Env,
    load_config,
    replace_config,
    save_config,
)
from bizviz.context import AppContext
from bizviz.db.connection import WrongKeyError, get_setting, set_setting, write_unit
from bizviz.db.migrate import migrate
from bizviz.platform import UnsupportedPlatformError, get_platform
from bizviz.platform.base import Platform

EXIT_ABORTED = 1
EXIT_BAD_LOCATION = 2
EXIT_KEY_MISSING = 3
EXIT_BUILD_FAILED = 4
EXIT_RESTORE_REFUSED = 5
EXIT_BAD_PHRASE = 6

PASSWORD_ATTEMPTS = 3
PHRASE_ATTEMPTS = 5
CA_DESKTOP_NAME = "BizViz-CA.cer"


class Prompter(Protocol):
    def ask(self, prompt: str) -> str: ...

    def secret(self, prompt: str) -> str: ...


class Runner(Protocol):
    def run(self, args: Sequence[str], cwd: Path) -> int: ...


class ConsolePrompter:
    def ask(self, prompt: str) -> str:
        return input(prompt)

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)


class SubprocessRunner:
    def run(self, args: Sequence[str], cwd: Path) -> int:
        try:
            return subprocess.run(list(args), cwd=cwd, check=False).returncode
        except FileNotFoundError:
            print(f"Command not found: {args[0]}", file=sys.stderr)
            return 127


class _Abort(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _Cli:
    env: Env
    cfg: AppConfig
    platform: Platform
    prompter: Prompter
    runner: Runner
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def production(self) -> bool:
        return self.env == "production"

    def db_key(self) -> str:
        key = self.platform.secrets.get(KC_DB_KEY)
        if not key:
            raise _Abort(
                EXIT_KEY_MISSING,
                "The database key is missing from the Keychain. Run `bizviz setup`.",
            )
        return key

    def ctx(self) -> AppContext:
        return AppContext(cfg=self.cfg, platform=self.platform, db_key_hex=self.db_key())

    def confirm(self, prompt: str) -> bool:
        return self.prompter.ask(prompt).strip().lower() in ("y", "yes")


def repo_location_problem(repo_dir: Path, home: Path) -> str | None:
    repo = repo_dir.resolve()
    for name in (
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "Library" / "Mobile Documents",
    ):
        if repo.is_relative_to(name.resolve()):
            return (
                f"BizViz cannot run from {name}: the background service is not allowed to read "
                "files there. Move the repository to ~/bizviz or ~/Developer/bizviz and run "
                "`bizviz setup` again."
            )
    return None


def _desktop_export_allowed(cfg: AppConfig) -> bool:
    return (
        cfg.env == "production"
        and not os.environ.get("BIZVIZ_HOME")
        and os.environ.get("BIZVIZ_PLATFORM") != "fake"
    )


def export_ca_to_desktop(cfg: AppConfig, home: Path) -> Path | None:
    dest = home / "Desktop" / CA_DESKTOP_NAME
    try:
        certs.export_ca_for_airdrop(cfg, dest)
    except OSError:
        print(f"Could not copy the CA certificate to {dest}.", file=sys.stderr)
        return None
    return dest


def _prompt_new_password(cli: _Cli) -> str:
    for _ in range(PASSWORD_ATTEMPTS):
        first = cli.prompter.secret("Dashboard password: ")
        second = cli.prompter.secret("Repeat the password: ")
        if first != second:
            print("The passwords do not match. Try again.", file=sys.stderr)
            continue
        try:
            validate_password(first)
        except ValueError:
            print("That password is too short. Try again.", file=sys.stderr)
            continue
        return first
    raise _Abort(EXIT_ABORTED, "No valid password was entered.")


def _warn_system(cli: _Cli) -> None:
    if cli.platform.system.filevault_enabled() is False:
        print(
            "Warning: FileVault is off. Your financial data is stored on this disk; "
            "turn on FileVault in System Settings.",
            file=sys.stderr,
        )
    if cli.platform.system.time_machine_configured() is False:
        print(
            "Warning: Time Machine is not configured, so the database backups are not "
            "copied anywhere else.",
            file=sys.stderr,
        )


def _ensure_plaid_credentials(cli: _Cli, force: bool) -> None:
    secrets = cli.platform.secrets
    if not force and secrets.get(KC_PLAID_CLIENT_ID) and secrets.get(KC_PLAID_SECRET):
        return
    client_id = cli.prompter.ask("Plaid client_id: ").strip()
    secret = cli.prompter.secret("Plaid secret: ").strip()
    if not client_id or not secret:
        raise _Abort(EXIT_ABORTED, "Both the Plaid client_id and secret are required.")
    secrets.set(KC_PLAID_CLIENT_ID, client_id)
    secrets.set(KC_PLAID_SECRET, secret)


def _show_phrase_and_confirm(cli: _Cli, key_hex: str) -> None:
    phrase = encode_recovery_phrase(key_hex)
    last_group = phrase.rsplit("-", 1)[-1]
    bar = "=" * 64
    print(f"\n{bar}")
    print("RECOVERY PHRASE")
    print()
    print(f"  {phrase}")
    print()
    print("This is the only way to unlock your data if this Mac is lost or the Keychain is")
    print("reset. Write it down or put it in a password manager, and keep it OUTSIDE this")
    print("Mac. It is shown only once.")
    print(f"{bar}\n")
    for _ in range(PHRASE_ATTEMPTS):
        typed = cli.prompter.ask("Type the LAST group of the phrase to confirm you saved it: ")
        if typed.strip().upper() == last_group:
            return
        print("That does not match the last group. Try again.", file=sys.stderr)
    raise _Abort(EXIT_ABORTED, "Recovery phrase not confirmed. Run `bizviz setup` again to finish.")


def _setup_database(cli: _Cli) -> AppContext:
    cfg, secrets = cli.cfg, cli.platform.secrets
    key = secrets.get(KC_DB_KEY)
    db_exists = cfg.db_path.exists()
    if not key and db_exists:
        raise _Abort(
            EXIT_KEY_MISSING,
            "Your database exists but its key is missing from the Keychain. Run "
            "`bizviz setup --recover-key` or `bizviz setup --restore <backup>`.",
        )
    if not key:
        import secrets as stdlib_secrets

        key = stdlib_secrets.token_hex(32)
        secrets.set(KC_DB_KEY, key)
    ctx = AppContext(cfg=cfg, platform=cli.platform, db_key_hex=key)
    password: str | None = None
    if not db_exists:
        password = _prompt_new_password(cli)
    try:
        conn = ctx.connect()
    except WrongKeyError:
        raise _Abort(
            EXIT_KEY_MISSING,
            "The Keychain key does not open the database. Run `bizviz setup --recover-key`.",
        ) from None
    try:
        cfg.db_path.chmod(0o600)
        migrate(conn, pre_migration_backup=make_premigration_hook(ctx, conn))
        if get_setting(conn, "setup_completed_at") is None:
            if get_setting(conn, "password_hash") is None:
                set_dashboard_password(conn, password or _prompt_new_password(cli))
            _show_phrase_and_confirm(cli, key)
            with write_unit(conn):
                set_setting(conn, "setup_completed_at", cli.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    finally:
        conn.close()
    return ctx


def _ensure_certs(cli: _Cli) -> None:
    cfg, secrets = cli.cfg, cli.platform.secrets
    now = cli.now()
    newly_pinned = False
    if cfg.hostname is None:
        cfg = replace_config(cfg, hostname=cli.platform.system.local_hostname())
        save_config(cfg)
        cli.cfg = cfg
        newly_pinned = True
    ca_present = certs.ca_path(cfg).exists() and secrets.get(KC_CA_KEY) is not None
    if not ca_present:
        certs.ensure_ca(cfg, secrets, now)
        certs.issue_leaf(cfg, secrets, now)
        _trust_and_export(cli)
    elif newly_pinned or certs.leaf_needs_renewal(cfg, now):
        certs.issue_leaf(cfg, secrets, now)


def _trust_and_export(cli: _Cli) -> None:
    cfg = cli.cfg
    cli.platform.trust.trust_ca(certs.ca_path(cfg))
    if _desktop_export_allowed(cfg):
        dest = export_ca_to_desktop(cfg, Path.home())
        if dest is not None:
            print(f"Copied the CA certificate to {dest} (AirDrop it to your iPhone).")


def _build_frontend(cli: _Cli, was_running: bool) -> None:
    web = cli.cfg.repo_dir / "web"
    for command in (["npm", "ci"], ["npm", "run", "build"]):
        code = cli.runner.run(command, web)
        if code == 0:
            continue
        print(
            f"The frontend build failed: `{' '.join(command)}` exited with {code}.", file=sys.stderr
        )
        if cli.production and was_running and (cli.cfg.web_dist / "index.html").exists():
            cli.platform.service.start()
            print("The previous build was restarted.", file=sys.stderr)
        raise _Abort(EXIT_BUILD_FAILED, "Setup stopped before installing the service.")


def _recover_key_command(cli: _Cli) -> int:
    phrase = cli.prompter.secret("Recovery phrase: ")
    try:
        recover_key(cli.cfg, cli.platform.secrets, phrase)
    except FileNotFoundError:
        raise _Abort(EXIT_ABORTED, f"No database found at {cli.cfg.db_path}.") from None
    except InvalidPhrase:
        raise _Abort(
            EXIT_BAD_PHRASE, "That recovery phrase does not open the existing database."
        ) from None
    print("The database key is stored in the Keychain again. No files were changed.")
    return 0


def _restore_command(cli: _Cli, backup: str, force: bool) -> None:
    cfg, secrets = cli.cfg, cli.platform.secrets
    backup_file = Path(backup).expanduser()
    refused = (
        f"A database already exists at {cfg.db_path}. Nothing was changed. To replace it with "
        "the backup, run again with --force: the old database, its backups folder and its key "
        "are kept under .bak-<timestamp> names."
    )
    if not backup_file.is_file():
        raise _Abort(EXIT_ABORTED, f"Backup file not found: {backup_file}")
    if cfg.db_path.exists() and not force:
        raise _Abort(EXIT_RESTORE_REFUSED, refused)

    was_running = cli.production and cli.platform.service.is_running()
    if was_running:
        cli.platform.service.stop()

    def give_up(code: int, message: str) -> _Abort:
        if was_running:
            cli.platform.service.start()
        return _Abort(code, message)

    phrase = cli.prompter.secret("Recovery phrase: ")
    try:
        result = restore_backup(cfg, secrets, backup_file, phrase, force=force, now=cli.now())
    except RestoreRefused:
        raise give_up(EXIT_RESTORE_REFUSED, refused) from None
    except InvalidPhrase:
        raise give_up(
            EXIT_BAD_PHRASE,
            "That recovery phrase does not open this backup. Nothing was changed.",
        ) from None
    except (FileNotFoundError, ValueError) as exc:
        raise give_up(EXIT_ABORTED, f"The backup could not be restored ({exc}).") from None

    for label, moved in (
        ("old database", result.moved_db),
        ("old backups folder", result.moved_backups),
        ("old database key (Keychain entry)", result.old_key_entry),
    ):
        if moved is not None:
            print(f"Kept the {label}: {moved}")

    ctx = cli.ctx()
    conn = ctx.connect()
    try:
        migrate(conn, pre_migration_backup=make_premigration_hook(ctx, conn))
        revoke_all(conn)
        if cli.confirm("Set a new dashboard password? [y/N] "):
            set_dashboard_password(conn, _prompt_new_password(cli))
        rows = conn.execute("SELECT * FROM items ORDER BY item_id").fetchall()
    finally:
        conn.close()
    tokens.index_merge(secrets, [tokens.db_index_entry(r) for r in rows])
    tokens.apply_index_removed_at(ctx)
    tokens.mirror_all_from_db(ctx)
    recovered = tokens.recover_tokens(ctx)
    if recovered:
        print(f"Re-adopted {len(recovered)} connection(s) from the Keychain index.")
    print("Restore complete; all dashboard sessions were signed out.")


def _setup_command(cli: _Cli, args: argparse.Namespace) -> int:
    problem = repo_location_problem(cli.cfg.repo_dir, Path.home())
    if problem:
        raise _Abort(EXIT_BAD_LOCATION, problem)
    _warn_system(cli)
    if args.recover_key:
        return _recover_key_command(cli)

    restored = False
    if args.restore:
        _restore_command(cli, args.restore, args.force)
        restored = True
    _ensure_plaid_credentials(cli, args.reconfigure_plaid)
    ctx = cli.ctx() if restored else _setup_database(cli)
    _ensure_certs(cli)
    ctx = AppContext(cfg=cli.cfg, platform=cli.platform, db_key_hex=ctx.db_key_hex)

    was_running = cli.production and cli.platform.service.is_running()
    if was_running:
        cli.platform.service.stop()
    _build_frontend(cli, was_running)
    recovered = tokens.recover_tokens(ctx)
    if recovered:
        print(f"Re-adopted {len(recovered)} connection(s) from the Keychain index.")
    cfg = cli.cfg
    if cli.production:
        cli.platform.service.install(
            cfg.repo_dir / ".venv" / "bin" / "python", cfg.repo_dir, cfg.log_dir
        )
    print()
    print("BizViz is set up.")
    print(f"  This Mac:  https://localhost:{cfg.port}")
    print(f"  Your LAN:  https://{cfg.hostname}:{cfg.port}")
    print("See docs/iphone-setup.md to add it to your iPhone.")
    if cli.production:
        print("macOS may ask whether Python may accept incoming network connections: choose Allow.")
    else:
        print(f"Sandbox has no background service. Run: bizviz --env {cfg.env} serve")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bizviz")
    parser.add_argument("--env", choices=["production", "sandbox"], default="production")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="set up or update BizViz (safe to re-run)")
    setup.add_argument("--restore", metavar="FILE", help="restore from a backup file")
    setup.add_argument(
        "--force", action="store_true", help="allow --restore over an existing database"
    )
    setup.add_argument(
        "--recover-key", action="store_true", help="re-store the key from the phrase"
    )
    setup.add_argument(
        "--reconfigure-plaid", action="store_true", help="re-enter Plaid credentials"
    )

    sub.add_parser("version", help="print the version")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    prompter: Prompter | None = None,
    runner: Runner | None = None,
    platform: Platform | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    env: Env = args.env
    try:
        if args.command == "version":
            print(__version__)
            return 0

        cfg = load_config(env)
        try:
            resolved = platform or get_platform(cfg)
        except UnsupportedPlatformError as exc:
            raise _Abort(EXIT_ABORTED, str(exc)) from None
        cli = _Cli(
            env=env,
            cfg=cfg,
            platform=resolved,
            prompter=prompter or ConsolePrompter(),
            runner=runner or SubprocessRunner(),
        )
        match args.command:
            case "setup":
                return _setup_command(cli, args)
    except _Abort as abort:
        print(abort.message, file=sys.stderr)
        return abort.code
    except SystemExit as exit_:
        if isinstance(exit_.code, str):
            print(exit_.code, file=sys.stderr)
            return EXIT_ABORTED
        raise
    except KeyboardInterrupt:
        return 130
    return EXIT_ABORTED

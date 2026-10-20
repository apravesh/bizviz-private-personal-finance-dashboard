import threading
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request

from bizviz import tokens
from bizviz.api.deps import ConnDep, RuntimeDep, require_csrf, require_session
from bizviz.api.schema import SystemResponse
from bizviz.certs import ca_expiring, cert_info
from bizviz.db.connection import get_setting
from bizviz.platform.base import SystemStatus

router = APIRouter(dependencies=[Depends(require_session), Depends(require_csrf)])

CACHE_SECONDS = 600


class HostFacts:
    """FileVault / Time Machine / hostname shell-outs, cached for ten minutes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = 0.0
        self._facts: tuple[bool | None, bool | None, str | None] | None = None

    def get(self, system: SystemStatus) -> tuple[bool | None, bool | None, str | None]:
        with self._lock:
            if self._facts is None or time.monotonic() - self._at > CACHE_SECONDS:
                self._facts = (
                    system.filevault_enabled(),
                    system.time_machine_configured(),
                    system.local_hostname() or None,
                )
                self._at = time.monotonic()
            return self._facts


@router.get("/api/system")
def system(request: Request, runtime: RuntimeDep, conn: ConnDep) -> SystemResponse:
    ctx = runtime.ctx
    filevault, time_machine, current_hostname = request.app.state.host_facts.get(
        ctx.platform.system
    )
    info = cert_info(ctx.cfg)
    hostname = ctx.cfg.hostname
    locked = runtime.rate_limiter.global_locked_until()
    return {
        "app_version": runtime.app_version,
        "filevault": filevault,
        "time_machine": time_machine,
        "cert_expires_at": info["leaf_expires_at"],
        "ca_expires_at": info["ca_expires_at"],
        "ca_expiring": ca_expiring(ctx.cfg, ctx.now()),
        "hostname": hostname,
        "current_hostname": current_hostname,
        "hostname_mismatch": bool(hostname and current_hostname and hostname != current_hostname),
        "plaid_env": ctx.cfg.plaid_env,
        "plaid_configured": ctx.plaid is not None,
        "plaid_items_created_count": int(get_setting(conn, "plaid_items_created_count") or 0),
        "recovered_items": [
            str(e.get("institution_name") or "Unknown institution")
            for e in tokens.recovered_items(conn)
        ],
        "login_locked_until": (
            None
            if locked is None
            else datetime.fromtimestamp(locked, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ),
    }

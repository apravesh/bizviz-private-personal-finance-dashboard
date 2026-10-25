from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from bizviz.auth import SessionInfo, validate_session
from bizviz.db.connection import Connection
from bizviz.plaid_types import PlaidClient
from bizviz.service import Runtime


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


class PlaidNotConfigured(Exception):
    pass


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def get_conn(runtime: RuntimeDep) -> Iterator[Connection]:
    conn = runtime.ctx.connect()
    try:
        yield conn
    finally:
        conn.close()


ConnDep = Annotated[Connection, Depends(get_conn)]


def require_session(request: Request, runtime: RuntimeDep, conn: ConnDep) -> SessionInfo:
    token = request.cookies.get(runtime.ctx.cfg.session_cookie)
    info = validate_session(conn, token, runtime.ctx.now()) if token else None
    if info is None:
        raise ApiError(401, "unauthorized", "Not signed in")
    return info


SessionDep = Annotated[SessionInfo, Depends(require_session)]


def require_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("x-bizviz") != "1":
        raise ApiError(403, "csrf", "Missing X-BizViz header")
    origin = request.headers.get("origin")
    if origin is not None and origin != f"https://{request.headers.get('host', '')}":
        raise ApiError(403, "csrf", "Origin not allowed")


def require_plaid(runtime: RuntimeDep) -> PlaidClient:
    if runtime.ctx.plaid is None:
        raise PlaidNotConfigured
    return runtime.ctx.plaid

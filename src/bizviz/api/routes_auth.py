import math
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from bizviz.api.deps import (
    ApiError,
    ConnDep,
    RuntimeDep,
    SessionDep,
    require_csrf,
    require_session,
)
from bizviz.auth import (
    SESSION_MAX_AGE,
    check_dashboard_password,
    create_session,
    list_sessions,
    revoke_all,
    revoke_session,
    set_dashboard_password,
    validate_password,
)

public_router = APIRouter(dependencies=[Depends(require_csrf)])
router = APIRouter(dependencies=[Depends(require_session), Depends(require_csrf)])

_COOKIE_FLAGS = "HttpOnly; Secure; SameSite=Strict; Path=/"


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str
    device_name: Annotated[str, Field(max_length=64)] = ""


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str


@public_router.post("/api/auth/login", status_code=204)
def login(body: LoginBody, request: Request, runtime: RuntimeDep, conn: ConnDep) -> Response:
    limiter = runtime.rate_limiter
    ip = request.client.host if request.client else "unknown"
    wait = limiter.check(ip)
    if wait is not None:
        minutes = max(1, math.ceil(wait / 60))
        raise ApiError(429, "rate_limited", f"Try again in {minutes} minutes")
    if not check_dashboard_password(conn, body.password):
        limiter.record_failure(ip)
        raise ApiError(401, "bad_password", "Wrong password")
    limiter.record_success(ip)
    token, _ = create_session(
        conn,
        body.device_name.strip() or None,
        request.headers.get("user-agent"),
        runtime.ctx.now(),
    )
    response = Response(status_code=204)
    response.headers.append(
        "set-cookie",
        f"{runtime.ctx.cfg.session_cookie}={token}; {_COOKIE_FLAGS}; Max-Age={SESSION_MAX_AGE}",
    )
    return response


@router.post("/api/auth/logout", status_code=204)
def logout(session: SessionDep, runtime: RuntimeDep, conn: ConnDep) -> Response:
    revoke_session(conn, session.session_id)
    response = Response(status_code=204)
    cookie = runtime.ctx.cfg.session_cookie
    response.headers.append("set-cookie", f"{cookie}=; {_COOKIE_FLAGS}; Max-Age=0")
    return response


@router.get("/api/session")
def get_session(session: SessionDep) -> dict[str, str | None]:
    return {"device_name": session.device_name, "created_at": session.created_at}


@router.post("/api/auth/password", status_code=204)
def change_password(body: PasswordBody, session: SessionDep, conn: ConnDep) -> Response:
    # 400, not 401: the frontend treats every 401 as "session invalid".
    if not check_dashboard_password(conn, body.current_password):
        raise ApiError(400, "bad_password", "Current password is wrong")
    validate_password(body.new_password)
    set_dashboard_password(conn, body.new_password)
    revoke_all(conn, except_session_id=session.session_id)
    return Response(status_code=204)


@router.get("/api/devices")
def devices(session: SessionDep, conn: ConnDep) -> list[dict[str, object]]:
    return [{**s, "current": s["session_id"] == session.session_id} for s in list_sessions(conn)]


@router.delete("/api/devices/{session_id}", status_code=204)
def delete_device(session_id: int, conn: ConnDep) -> Response:
    revoke_session(conn, session_id)
    return Response(status_code=204)

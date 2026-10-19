import logging
import mimetypes
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from bizviz.api import routes_auth, routes_data, routes_system
from bizviz.api.deps import ApiError
from bizviz.api.security import RequestLogMiddleware, SecurityHeadersMiddleware
from bizviz.errors import InvalidInputError, NotFoundError
from bizviz.service import Runtime

log = logging.getLogger(__name__)

mimetypes.add_type("application/manifest+json", ".webmanifest")

NONCE_PLACEHOLDER = "__CSP_NONCE__"
NOT_BUILT_HTML = (
    "<!doctype html><title>BizViz</title><p>Frontend not built — run <code>bizviz setup</code>.</p>"
)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _code_from_message(message: str) -> str:
    return (
        message
        if message and message.replace("_", "").isalnum() and message.islower()
        else "invalid"
    )


def _register_error_handlers(app: FastAPI) -> None:
    def api_error(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, ApiError)
        return _error(exc.status, exc.code, exc.message)

    def not_found(_: Request, exc: Exception) -> Response:
        return _error(404, "not_found", "Not found")

    def invalid_input(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, InvalidInputError)
        return _error(400, exc.code, "Invalid input")

    def value_error(_: Request, exc: Exception) -> Response:
        return _error(400, _code_from_message(str(exc)), "Invalid input")

    def validation_error(_: Request, exc: Exception) -> Response:
        return _error(400, "invalid", "Invalid request")

    def http_error(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error(exc.status_code, code, "Request failed")

    def unexpected(_: Request, exc: Exception) -> Response:
        log.error("unhandled error error_type=%s", type(exc).__name__)
        return _error(500, "internal_error", "Internal error")

    handlers: list[tuple[Any, Any]] = [
        (ApiError, api_error),
        (NotFoundError, not_found),
        (InvalidInputError, invalid_input),
        (ValueError, value_error),
        (RequestValidationError, validation_error),
        (StarletteHTTPException, http_error),
        (Exception, unexpected),
    ]
    for exc_type, handler in handlers:
        app.add_exception_handler(exc_type, handler)


def create_app(runtime: Runtime) -> FastAPI:
    ctx = runtime.ctx
    ctx.request_sync = runtime.scheduler.request_sync

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runtime.scheduler.start()
        try:
            yield
        finally:
            runtime.scheduler.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    app.state.host_facts = routes_system.HostFacts()
    _register_error_handlers(app)

    app.include_router(routes_auth.public_router)
    app.include_router(routes_auth.router)
    app.include_router(routes_data.router)
    app.include_router(routes_system.router)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": runtime.app_version}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(path: str) -> Response:
        return _error(404, "not_found", "Not found")

    web_dist = ctx.cfg.web_dist

    def index(request: Request) -> Response:
        try:
            html = (web_dist / "index.html").read_text(encoding="utf-8")
        except OSError:
            return HTMLResponse(NOT_BUILT_HTML, status_code=503)
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        return HTMLResponse(
            html.replace(NONCE_PLACEHOLDER, nonce), headers={"Cache-Control": "no-cache"}
        )

    app.add_api_route("/", index, methods=["GET"], include_in_schema=False)
    app.add_api_route("/index.html", index, methods=["GET"], include_in_schema=False)
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist), name="static")

    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SecurityHeadersMiddleware, plaid_env=ctx.cfg.plaid_env)
    app.add_middleware(RequestLogMiddleware)
    return app

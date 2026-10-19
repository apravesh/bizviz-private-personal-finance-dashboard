"""Pure-ASGI middleware: security headers, CSP and privacy-safe request logging."""

import logging
import time
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PLAID_LINK_SCRIPT = "https://cdn.plaid.com/link/v2/stable/link-initialize.js"

log = logging.getLogger("bizviz.request")


def build_csp(plaid_env: str, nonce: str | None) -> str:
    script = ["'self'"]
    if nonce:
        script.append(f"'nonce-{nonce}'")
    script.append(PLAID_LINK_SCRIPT)
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {' '.join(script)}",
            "style-src 'self' 'unsafe-inline'",
            "style-src-elem 'self' 'unsafe-inline'",
            "style-src-attr 'unsafe-inline'",
            "frame-src https://cdn.plaid.com/",
            f"connect-src 'self' https://{plaid_env}.plaid.com/",
            "img-src 'self' data:",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, plaid_env: str) -> None:
        self.app = app
        self.plaid_env = plaid_env

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                nonce = scope.get("state", {}).get("csp_nonce")
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Content-Security-Policy"] = build_csp(self.plaid_env, nonce)
                if is_api:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestLogMiddleware:
    """Logs method, route template, status and duration only; never paths, queries or bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route: Any = scope.get("route")
            template = getattr(route, "path", None) or "static"
            log.info(
                "%s %s %d %dms",
                scope["method"],
                template,
                status,
                int((time.monotonic() - started) * 1000),
            )

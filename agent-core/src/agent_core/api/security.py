"""Protection for the localhost API.

Listening on 127.0.0.1 is not enough: any web page open in the user's browser can
reach localhost, and browsers apply no CORS to WebSockets. So every request needs
the per-install token, a loopback Host (blocks DNS rebinding) and, if it carries
an Origin, an explicitly allowed one (blocks cross-site pages).
"""

from __future__ import annotations

import secrets
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def load_or_create_token(path: Path) -> str:
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def bearer_token(header: str | None) -> str | None:
    if header and header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return None


def token_matches(provided: str | None, expected: str) -> bool:
    return provided is not None and secrets.compare_digest(provided.encode(), expected.encode())


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]}
            if problem := self._problem(headers):
                if scope["type"] == "http":
                    reject = JSONResponse({"detail": problem}, status_code=403)
                else:
                    reject = WebSocketClose(code=1008, reason=problem)  # handshake fails with HTTP 403
                await reject(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _problem(self, headers: dict[str, str]) -> str | None:
        host = headers.get("host", "")
        hostname = host[: host.find("]") + 1] if host.startswith("[") else host.rsplit(":", 1)[0]
        if hostname.lower() not in LOOPBACK_HOSTNAMES:
            return "Host not allowed"
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return "Origin not allowed"
        return None

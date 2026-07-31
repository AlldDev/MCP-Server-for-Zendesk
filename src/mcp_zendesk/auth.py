from __future__ import annotations

import hmac
import logging
import time
from typing import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 300.0


class BearerAuthMiddleware:
    """ASGI middleware requiring a per-client bearer token (API key) on every HTTP request.

    Independent of Zendesk credentials: this protects the MCP server itself,
    since it is exposed on the public internet (spec section 6.1). Each client
    has its own token, so one can be revoked without affecting the others.

    Failed attempts are rate-limited per source IP with exponential backoff: after
    max_attempts consecutive failures, each further failure doubles the lockout window
    (capped at MAX_BACKOFF_SECONDS) until a correct token is presented.
    """

    def __init__(
        self,
        app: ASGIApp,
        api_keys: dict[str, str],
        exempt_paths: frozenset[str] = frozenset(),
        max_attempts: int = 5,
        base_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._api_keys = api_keys  # token -> client_id
        self._exempt_paths = exempt_paths
        self._max_attempts = max_attempts
        self._base_seconds = base_seconds
        self._clock = clock
        # ponytail: unbounded dict, one entry per distinct source IP that ever failed —
        # fine for the expected volume (a handful of clients plus scanners the backoff
        # itself throttles); evict stale entries if this ever becomes a real memory issue.
        self._failures: dict[str, tuple[int, float]] = {}

    def _client_ip(self, scope: Scope) -> str:
        """Real client IP behind the Caddy reverse proxy, which sets X-Forwarded-For by default."""
        headers = dict(scope["headers"])
        forwarded = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt_paths:
            await self._app(scope, receive, send)
            return

        ip = self._client_ip(scope)
        now = self._clock()
        count, blocked_until = self._failures.get(ip, (0, 0.0))
        if blocked_until > now:
            response = JSONResponse(
                {"error": "too many attempts"},
                status_code=429,
                headers={"Retry-After": str(int(blocked_until - now) + 1)},
            )
            await response(scope, receive, send)
            return

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization", b"").decode()
        presented = auth_header[len("Bearer ") :] if auth_header.startswith("Bearer ") else ""
        client_id = self._resolve_client(presented)
        if client_id is None:
            count += 1
            delay = 0.0
            if count > self._max_attempts:
                delay = min(self._base_seconds * 2 ** (count - self._max_attempts - 1), MAX_BACKOFF_SECONDS)
            self._failures[ip] = (count, now + delay)
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        self._failures.pop(ip, None)
        logger.info("authenticated request client=%s path=%s", client_id, scope.get("path"))
        await self._app(scope, receive, send)

    def _resolve_client(self, presented: str) -> str | None:
        if not presented:
            return None
        for token, client_id in self._api_keys.items():
            if hmac.compare_digest(presented, token):
                return client_id
        return None

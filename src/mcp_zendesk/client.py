from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class ZendeskAPIError(Exception):
    """Raised for Zendesk API errors, carrying a message safe to show the caller."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _resource(path: str) -> str:
    """Top-level resource segment of a Zendesk API path, e.g. "tickets" for
    "/tickets/5.json" or "help_center" for "/help_center/articles/5.json"."""
    return path.strip("/").split("/", 1)[0].split(".", 1)[0]


class _TTLCache:
    """Tiny in-memory cache for GET responses. Cleared by resource on write.

    ponytail: invalidation is scoped by top-level resource segment, not per-ticket;
    split further if write throughput on a single resource type ever makes that matter.
    """

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._store: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

    def get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: tuple[Any, ...], value: dict[str, Any]) -> None:
        self._store[key] = (self._clock() + self._ttl, value)

    def clear(self, prefix: str | None = None) -> None:
        if prefix is None:
            self._store.clear()
        else:
            self._store = {k: v for k, v in self._store.items() if _resource(k[0]) != prefix}


class ZendeskClient:
    """Thin async wrapper around the Zendesk Support REST API."""

    def __init__(
        self,
        subdomain: str,
        client_id: str,
        client_secret: str,
        scope: str = "read write",
        http_client: httpx.AsyncClient | None = None,
        cache_ttl_seconds: float = 30.0,
        _clock: Callable[[], float] = time.monotonic,
    ):
        self._subdomain = subdomain
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._clock = _clock
        self._client = http_client or httpx.AsyncClient(
            base_url=f"https://{subdomain}.zendesk.com/api/v2",
            timeout=30.0,
        )
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._cache = _TTLCache(cache_ttl_seconds, _clock) if cache_ttl_seconds > 0 else None

    async def aclose(self) -> None:
        await self._client.aclose()

    def invalidate_cache(self, path: str | None = None) -> None:
        if self._cache is not None:
            self._cache.clear(_resource(path) if path is not None else None)

    async def _ensure_token(self, force: bool = False) -> None:
        """Fetch (or refresh) the OAuth access token via the client_credentials grant.

        No refresh_token is issued for this grant type; expiry just means asking for a
        new one. Lock is unconditional (not double-checked) since asyncio has no thread
        contention to avoid and the check itself is cheap.
        """
        async with self._token_lock:
            if not force and self._clock() < self._token_expires_at:
                return
            response = await self._send_with_retry(
                "POST",
                f"https://{self._subdomain}.zendesk.com/oauth/tokens",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                },
            )
            token = self._parse(response)
            self._client.headers["Authorization"] = f"Bearer {token['access_token']}"
            self._token_expires_at = self._clock() + token["expires_in"] - 30

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        await self._ensure_token()
        response = await self._send_with_retry(method, path, **kwargs)
        if response.status_code == 401:
            await self._ensure_token(force=True)
            response = await self._send_with_retry(method, path, **kwargs)
        return self._parse(response)

    async def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._cache is None:
            return await self.request("GET", path, **kwargs)
        cache_key = (path, tuple(sorted(kwargs.get("params", {}).items())))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self.request("GET", path, **kwargs)
        self._cache.set(cache_key, data)
        return data

    async def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        data = await self.request("POST", path, **kwargs)
        self.invalidate_cache(path)
        return data

    async def put(self, path: str, **kwargs: Any) -> dict[str, Any]:
        data = await self.request("PUT", path, **kwargs)
        self.invalidate_cache(path)
        return data

    async def _send_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response: httpx.Response
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = await self._client.request(method, path, **kwargs)
            if response.status_code != 429 or attempt == MAX_ATTEMPTS:
                return response
            delay = float(response.headers.get("Retry-After", 2**attempt))
            logger.warning("Zendesk rate limit hit (attempt %d/%d), retrying in %.1fs", attempt, MAX_ATTEMPTS, delay)
            await asyncio.sleep(delay)
        return response

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 401:
            raise ZendeskAPIError("Zendesk rejected the configured credentials (401).", status=401)
        if response.status_code == 403:
            raise ZendeskAPIError("Zendesk denied access to this resource (403).", status=403)
        if response.status_code == 404:
            raise ZendeskAPIError("Resource not found in Zendesk.", status=404)
        if response.status_code == 429:
            raise ZendeskAPIError("Zendesk rate limit exceeded; please retry later.", status=429)
        if response.status_code >= 500:
            raise ZendeskAPIError("Zendesk is temporarily unavailable; please retry later.", status=response.status_code)
        if response.status_code >= 400:
            raise ZendeskAPIError(
                f"Zendesk request failed with status {response.status_code}: {response.text[:500]}",
                status=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

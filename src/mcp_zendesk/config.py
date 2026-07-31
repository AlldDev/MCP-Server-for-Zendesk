from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

_REQUIRED_VARS = ("ZENDESK_SUBDOMAIN", "ZENDESK_OAUTH_CLIENT_ID", "ZENDESK_OAUTH_CLIENT_SECRET", "MCP_SERVER_API_KEYS")


class _JSONLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    """Structured (JSON) logging, level from LOG_LEVEL (default INFO). Never log tokens or PII."""
    handler = logging.StreamHandler()
    handler.setFormatter(_JSONLogFormatter())
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), handlers=[handler], force=True)


@dataclass(frozen=True)
class Settings:
    zendesk_subdomain: str
    zendesk_oauth_client_id: str
    zendesk_oauth_client_secret: str
    zendesk_oauth_scope: str
    api_keys: dict[str, str]  # token -> client_id, for individual revocation per client
    cache_ttl_seconds: float
    zendesk_webhook_secret: str | None  # webhook support disabled if unset
    auth_rate_limit_max_attempts: int
    auth_rate_limit_base_seconds: float


def _load_api_keys() -> dict[str, str]:
    """MCP_SERVER_API_KEYS is a JSON object of {client_id: token}, inverted here to {token: client_id}."""
    try:
        client_tokens = json.loads(os.environ["MCP_SERVER_API_KEYS"])
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP_SERVER_API_KEYS must be a JSON object of {client_id: token}") from exc
    if not client_tokens or not all(isinstance(k, str) and isinstance(v, str) for k, v in client_tokens.items()):
        raise RuntimeError("MCP_SERVER_API_KEYS must be a non-empty JSON object of {client_id: token}")
    return {token: client_id for client_id, token in client_tokens.items()}


def load_settings() -> Settings:
    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    return Settings(
        zendesk_subdomain=os.environ["ZENDESK_SUBDOMAIN"],
        zendesk_oauth_client_id=os.environ["ZENDESK_OAUTH_CLIENT_ID"],
        zendesk_oauth_client_secret=os.environ["ZENDESK_OAUTH_CLIENT_SECRET"],
        zendesk_oauth_scope=os.environ.get("ZENDESK_OAUTH_SCOPE", "read write"),
        api_keys=_load_api_keys(),
        cache_ttl_seconds=float(os.environ.get("CACHE_TTL_SECONDS", "30")),
        zendesk_webhook_secret=os.environ.get("ZENDESK_WEBHOOK_SECRET") or None,
        auth_rate_limit_max_attempts=int(os.environ.get("AUTH_RATE_LIMIT_MAX_ATTEMPTS", "5")),
        auth_rate_limit_base_seconds=float(os.environ.get("AUTH_RATE_LIMIT_BASE_SECONDS", "1")),
    )

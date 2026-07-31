from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from mcp_zendesk.client import ZendeskClient

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/zendesk"


def _verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    expected = base64.b64encode(hmac.new(secret.encode(), timestamp.encode() + body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


def create_webhook_route(client: ZendeskClient, secret: str) -> Route:
    """Zendesk webhook receiver: verifies the signature, then invalidates the read
    cache so ticket events are reflected on the next read (spec section 9, Fase 4)."""

    async def handle(request: Request) -> Response:
        body = await request.body()
        signature = request.headers.get("x-zendesk-webhook-signature", "")
        timestamp = request.headers.get("x-zendesk-webhook-signature-timestamp", "")
        if not signature or not timestamp or not _verify_signature(secret, timestamp, body, signature):
            return Response(status_code=401)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(status_code=400)

        logger.info("zendesk webhook event=%s", payload.get("type", "unknown"))
        client.invalidate_cache()
        return Response(status_code=204)

    return Route(WEBHOOK_PATH, handle, methods=["POST"])

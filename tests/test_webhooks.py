import base64
import hashlib
import hmac
import json

from starlette.applications import Starlette
from starlette.testclient import TestClient

from mcp_zendesk.webhooks import create_webhook_route

SECRET = "webhook-secret"


class FakeZendeskClient:
    def __init__(self):
        self.invalidated = False

    def invalidate_cache(self):
        self.invalidated = True


def sign(timestamp: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), timestamp.encode() + body, hashlib.sha256).digest()).decode()


def make_test_client(fake_client: FakeZendeskClient) -> TestClient:
    app = Starlette(routes=[create_webhook_route(fake_client, SECRET)])
    return TestClient(app)


def test_valid_signature_invalidates_cache_and_returns_204():
    fake_client = FakeZendeskClient()
    body = json.dumps({"type": "ticket.updated"}).encode()
    timestamp = "2026-07-30T12:00:00Z"
    response = make_test_client(fake_client).post(
        "/webhooks/zendesk",
        content=body,
        headers={
            "x-zendesk-webhook-signature": sign(timestamp, body),
            "x-zendesk-webhook-signature-timestamp": timestamp,
        },
    )
    assert response.status_code == 204
    assert fake_client.invalidated is True


def test_missing_signature_rejected():
    fake_client = FakeZendeskClient()
    response = make_test_client(fake_client).post("/webhooks/zendesk", content=b"{}")
    assert response.status_code == 401
    assert fake_client.invalidated is False


def test_wrong_signature_rejected():
    fake_client = FakeZendeskClient()
    body = json.dumps({"type": "ticket.updated"}).encode()
    response = make_test_client(fake_client).post(
        "/webhooks/zendesk",
        content=body,
        headers={
            "x-zendesk-webhook-signature": "bogus",
            "x-zendesk-webhook-signature-timestamp": "2026-07-30T12:00:00Z",
        },
    )
    assert response.status_code == 401
    assert fake_client.invalidated is False

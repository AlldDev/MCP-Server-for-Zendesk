from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_zendesk.auth import BearerAuthMiddleware


async def _ok(request):
    return PlainTextResponse("ok")


def make_test_client() -> TestClient:
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice", "bob-token": "bob"})
    return TestClient(app)


def test_missing_authorization_header_rejected():
    response = make_test_client().get("/")
    assert response.status_code == 401


def test_wrong_token_rejected():
    response = make_test_client().get("/", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_correct_token_accepted():
    response = make_test_client().get("/", headers={"Authorization": "Bearer alice-token"})
    assert response.status_code == 200
    assert response.text == "ok"


def test_a_second_clients_own_token_is_also_accepted():
    response = make_test_client().get("/", headers={"Authorization": "Bearer bob-token"})
    assert response.status_code == 200


def test_revoking_one_clients_token_does_not_affect_the_other():
    app = BearerAuthMiddleware(Starlette(routes=[Route("/", _ok)]), api_keys={"bob-token": "bob"})
    response = TestClient(app).get("/", headers={"Authorization": "Bearer alice-token"})
    assert response.status_code == 401


def test_exempt_path_bypasses_bearer_check():
    inner = Starlette(routes=[Route("/webhooks/zendesk", _ok, methods=["POST"])])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice"}, exempt_paths=frozenset({"/webhooks/zendesk"}))
    response = TestClient(app).post("/webhooks/zendesk")
    assert response.status_code == 200


def test_non_exempt_path_still_requires_bearer_token():
    inner = Starlette(routes=[Route("/", _ok), Route("/webhooks/zendesk", _ok, methods=["POST"])])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice"}, exempt_paths=frozenset({"/webhooks/zendesk"}))
    response = TestClient(app).get("/")
    assert response.status_code == 401


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_rate_limit_blocks_after_max_attempts():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice"}, max_attempts=3, base_seconds=1.0, clock=clock)
    client = TestClient(app)
    for _ in range(4):
        response = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
        assert response.status_code == 401
    response = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_correct_token_resets_the_counter():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice"}, max_attempts=3, base_seconds=1.0, clock=clock)
    client = TestClient(app)
    for _ in range(3):
        client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    response = client.get("/", headers={"Authorization": "Bearer alice-token", "X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 200
    response = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 401


def test_rate_limit_is_tracked_per_ip():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice"}, max_attempts=3, base_seconds=1.0, clock=clock)
    client = TestClient(app)
    for _ in range(4):
        client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    blocked = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    assert blocked.status_code == 429
    other_ip = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "5.6.7.8"})
    assert other_ip.status_code == 401


def test_rate_limit_unblocks_after_backoff_elapses():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(inner, api_keys={"alice-token": "alice"}, max_attempts=3, base_seconds=1.0, clock=clock)
    client = TestClient(app)
    for _ in range(4):
        client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    response = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 429

    clock.now += 10
    response = client.get("/", headers={"Authorization": "Bearer wrong-token", "X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 401


def test_client_rate_limit_disabled_by_default():
    response = make_test_client().get("/", headers={"Authorization": "Bearer alice-token"})
    assert response.status_code == 200


def test_client_rate_limit_blocks_after_max_requests():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(
        inner,
        api_keys={"alice-token": "alice"},
        client_rate_limit_max_requests=2,
        client_rate_limit_window_seconds=60.0,
        clock=clock,
    )
    client = TestClient(app)
    for _ in range(2):
        response = client.get("/", headers={"Authorization": "Bearer alice-token"})
        assert response.status_code == 200
    response = client.get("/", headers={"Authorization": "Bearer alice-token"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_client_rate_limit_is_tracked_per_client_not_globally():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(
        inner,
        api_keys={"alice-token": "alice", "bob-token": "bob"},
        client_rate_limit_max_requests=1,
        client_rate_limit_window_seconds=60.0,
        clock=clock,
    )
    client = TestClient(app)
    assert client.get("/", headers={"Authorization": "Bearer alice-token"}).status_code == 200
    assert client.get("/", headers={"Authorization": "Bearer alice-token"}).status_code == 429
    assert client.get("/", headers={"Authorization": "Bearer bob-token"}).status_code == 200


def test_client_rate_limit_resets_after_window_elapses():
    clock = FakeClock()
    inner = Starlette(routes=[Route("/", _ok)])
    app = BearerAuthMiddleware(
        inner,
        api_keys={"alice-token": "alice"},
        client_rate_limit_max_requests=1,
        client_rate_limit_window_seconds=60.0,
        clock=clock,
    )
    client = TestClient(app)
    assert client.get("/", headers={"Authorization": "Bearer alice-token"}).status_code == 200
    assert client.get("/", headers={"Authorization": "Bearer alice-token"}).status_code == 429
    clock.now += 61
    assert client.get("/", headers={"Authorization": "Bearer alice-token"}).status_code == 200

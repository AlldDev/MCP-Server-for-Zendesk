import httpx
import pytest
import respx

from mcp_zendesk.client import ZendeskAPIError, ZendeskClient

BASE_URL = "https://acme.zendesk.com/api/v2"
TOKEN_URL = "https://acme.zendesk.com/oauth/tokens"


def make_client(cache_ttl_seconds: float = 30.0, clock=None) -> ZendeskClient:
    kwargs = {"_clock": clock} if clock is not None else {}
    return ZendeskClient(
        subdomain="acme",
        client_id="test-client-id",
        client_secret="secret-token",
        cache_ttl_seconds=cache_ttl_seconds,
        **kwargs,
    )


def mock_oauth_token(expires_in=3600, access_token="tok-abc"):
    return respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            201, json={"access_token": access_token, "token_type": "bearer", "expires_in": expires_in, "scope": "read write"}
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_get_success():
    mock_oauth_token()
    respx.get(f"{BASE_URL}/tickets/1.json").mock(
        return_value=httpx.Response(200, json={"ticket": {"id": 1, "subject": "hi"}})
    )
    client = make_client()
    data = await client.get("/tickets/1.json")
    assert data == {"ticket": {"id": 1, "subject": "hi"}}


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_without_leaking_token():
    mock_oauth_token()
    respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(401, json={}))
    client = make_client()
    with pytest.raises(ZendeskAPIError) as exc_info:
        await client.get("/tickets/1.json")
    assert "secret-token" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_with_status_and_distinct_message():
    mock_oauth_token()
    respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(403, json={}))
    client = make_client()
    with pytest.raises(ZendeskAPIError) as exc_info:
        await client.get("/tickets/1.json")
    assert exc_info.value.status == 403
    assert "401" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_400_surfaces_response_body():
    mock_oauth_token()
    respx.get(f"{BASE_URL}/search.json").mock(
        return_value=httpx.Response(400, json={"error": {"title": "Invalid attribute", "message": "page must be an integer"}})
    )
    client = make_client()
    with pytest.raises(ZendeskAPIError) as exc_info:
        await client.get("/search.json")
    assert exc_info.value.status == 400
    assert "page must be an integer" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_404_raises():
    mock_oauth_token()
    respx.get(f"{BASE_URL}/tickets/999.json").mock(return_value=httpx.Response(404, json={}))
    client = make_client()
    with pytest.raises(ZendeskAPIError, match="not found"):
        await client.get("/tickets/999.json")


@pytest.mark.asyncio
@respx.mock
async def test_429_retries_then_succeeds():
    mock_oauth_token()
    route = respx.get(f"{BASE_URL}/tickets.json")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"tickets": []}),
    ]
    client = make_client()
    data = await client.get("/tickets.json")
    assert data == {"tickets": []}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_429_exhausted_raises():
    mock_oauth_token()
    respx.get(f"{BASE_URL}/tickets.json").mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
    client = make_client()
    with pytest.raises(ZendeskAPIError, match="rate limit"):
        await client.get("/tickets.json")


@pytest.mark.asyncio
@respx.mock
async def test_repeated_get_within_ttl_hits_cache_not_network():
    mock_oauth_token()
    route = respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    client = make_client(cache_ttl_seconds=30.0)
    await client.get("/tickets/1.json")
    await client.get("/tickets/1.json")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_after_ttl_expiry_hits_network_again():
    mock_oauth_token()
    route = respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    fake_time = [0.0]
    client = make_client(cache_ttl_seconds=10.0, clock=lambda: fake_time[0])
    await client.get("/tickets/1.json")
    fake_time[0] = 11.0
    await client.get("/tickets/1.json")
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_write_invalidates_the_cache():
    mock_oauth_token()
    get_route = respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    respx.put(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    client = make_client()
    await client.get("/tickets/1.json")
    await client.put("/tickets/1.json", json={"ticket": {"status": "open"}})
    await client.get("/tickets/1.json")
    assert get_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_write_does_not_invalidate_unrelated_resource_cache():
    mock_oauth_token()
    groups_route = respx.get(f"{BASE_URL}/groups.json").mock(
        return_value=httpx.Response(200, json={"groups": [{"id": 1}]})
    )
    respx.put(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    client = make_client()
    await client.get("/groups.json")
    await client.put("/tickets/1.json", json={"ticket": {"status": "open"}})
    await client.get("/groups.json")
    assert groups_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_zero_ttl_disables_caching():
    mock_oauth_token()
    route = respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    client = make_client(cache_ttl_seconds=0)
    await client.get("/tickets/1.json")
    await client.get("/tickets/1.json")
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_oauth_token_fetched_once_and_reused():
    token_route = mock_oauth_token()
    resource_route = respx.get(f"{BASE_URL}/tickets/1.json").mock(
        return_value=httpx.Response(200, json={"ticket": {"id": 1}})
    )
    client = make_client(cache_ttl_seconds=0)  # disable read cache so both calls hit the network
    await client.get("/tickets/1.json")
    await client.get("/tickets/1.json")
    assert token_route.call_count == 1
    assert resource_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_oauth_token_refreshed_after_expiry():
    token_route = respx.post(TOKEN_URL)
    token_route.side_effect = [
        httpx.Response(201, json={"access_token": "tok-1", "token_type": "bearer", "expires_in": 100, "scope": "read write"}),
        httpx.Response(201, json={"access_token": "tok-2", "token_type": "bearer", "expires_in": 100, "scope": "read write"}),
    ]
    respx.get(f"{BASE_URL}/tickets/1.json").mock(return_value=httpx.Response(200, json={"ticket": {"id": 1}}))
    fake_time = [0.0]
    client = make_client(clock=lambda: fake_time[0])
    await client.get("/tickets/1.json")
    fake_time[0] = 71.0  # past expires_in(100) - 30s early-refresh margin
    await client.get("/tickets/1.json")
    assert token_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_401_forces_token_refresh_and_retries():
    token_route = mock_oauth_token()
    resource_route = respx.get(f"{BASE_URL}/tickets/1.json")
    resource_route.side_effect = [
        httpx.Response(401, json={}),
        httpx.Response(200, json={"ticket": {"id": 1}}),
    ]
    client = make_client()
    data = await client.get("/tickets/1.json")
    assert data == {"ticket": {"id": 1}}
    assert resource_route.call_count == 2
    assert token_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_oauth_token_endpoint_error_raises_without_leaking_secret():
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
    client = make_client()
    with pytest.raises(ZendeskAPIError) as exc_info:
        await client.get("/tickets/1.json")
    assert "secret-token" not in str(exc_info.value)

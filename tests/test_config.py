import pytest

from mcp_zendesk.config import load_settings

BASE_ENV = {
    "ZENDESK_SUBDOMAIN": "acme",
    "ZENDESK_OAUTH_CLIENT_ID": "client-id",
    "ZENDESK_OAUTH_CLIENT_SECRET": "client-secret",
}


def test_missing_required_var_raises(monkeypatch):
    monkeypatch.delenv("MCP_SERVER_API_KEYS", raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="MCP_SERVER_API_KEYS"):
        load_settings()


def test_malformed_api_keys_json_raises(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_SERVER_API_KEYS", "not-json")
    with pytest.raises(RuntimeError, match="MCP_SERVER_API_KEYS"):
        load_settings()


def test_valid_api_keys_are_inverted_to_token_to_client(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_SERVER_API_KEYS", '{"alice": "tok-a", "bob": "tok-b"}')
    settings = load_settings()
    assert settings.api_keys == {"tok-a": "alice", "tok-b": "bob"}


def test_oauth_scope_defaults_to_read_write(monkeypatch):
    monkeypatch.delenv("ZENDESK_OAUTH_SCOPE", raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_SERVER_API_KEYS", '{"alice": "tok-a"}')
    settings = load_settings()
    assert settings.zendesk_oauth_scope == "read write"


def test_oauth_scope_honors_override(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_SERVER_API_KEYS", '{"alice": "tok-a"}')
    monkeypatch.setenv("ZENDESK_OAUTH_SCOPE", "read")
    settings = load_settings()
    assert settings.zendesk_oauth_scope == "read"


def test_client_rate_limit_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CLIENT_RATE_LIMIT_MAX_REQUESTS", raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_SERVER_API_KEYS", '{"alice": "tok-a"}')
    settings = load_settings()
    assert settings.client_rate_limit_max_requests is None
    assert settings.client_rate_limit_window_seconds == 60.0


def test_client_rate_limit_honors_override(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_SERVER_API_KEYS", '{"alice": "tok-a"}')
    monkeypatch.setenv("CLIENT_RATE_LIMIT_MAX_REQUESTS", "100")
    monkeypatch.setenv("CLIENT_RATE_LIMIT_WINDOW_SECONDS", "30")
    settings = load_settings()
    assert settings.client_rate_limit_max_requests == 100
    assert settings.client_rate_limit_window_seconds == 30.0

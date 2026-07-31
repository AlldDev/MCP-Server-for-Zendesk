# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MCP (Model Context Protocol) server exposing Zendesk Support operations as tools, meant to run remotely
over HTTP (not stdio) behind a reverse proxy, with per-client bearer tokens and IP allowlisting as
additional layers of defense. Full deployment/ops runbook is in `README.md` (Portuguese); functional spec
and design rationale are in `spec-mcp-zendesk.md` (also Portuguese).

## Commands

```bash
pip install -e ".[dev]"           # install with dev/test deps
pytest                             # run all tests
pytest tests/test_client.py -k test_401_forces_token_refresh_and_retries  # single test
uvicorn mcp_zendesk.server:app --host 0.0.0.0 --port 8080   # run the server directly
./scripts/run.sh                   # same, via the provided script
python scripts/generate_token.py   # generate a client API token for MCP_SERVER_API_KEYS
```

No lint/format tooling is configured in this repo.

Config comes from environment variables (loaded via `.env`, see `.env.example`); `ZENDESK_SUBDOMAIN`,
`ZENDESK_OAUTH_CLIENT_ID`, `ZENDESK_OAUTH_CLIENT_SECRET`, and `MCP_SERVER_API_KEYS` are required — the
server (`config.load_settings`) raises at startup if any are missing, so most local runs need a real
`.env` (or these exported) even just to import `mcp_zendesk.server`.

## Architecture

Request flow: `BearerAuthMiddleware` (auth.py) wraps the whole ASGI app → MCP's `streamable_http_app`
(mounted at `/mcp`) → tool functions in `server.py` → thin per-resource modules in `tools/` → shared
`ZendeskClient` (client.py) → Zendesk REST API.

- **`server.py`** is the composition root: builds the singleton `ZendeskClient` from `Settings`, registers
  every `@mcp.tool()`, mounts the webhook route (if configured), and wraps everything in
  `BearerAuthMiddleware`. Each `@mcp.tool()` function is a thin adapter that just calls into `tools/`.
- **`tools/*.py`** hold the actual per-resource logic (tickets, search, users, groups, guides) and take
  a `ZendeskClient` as their first argument — they're plain functions, not classes, and are what
  `tests/test_tools.py` exercises directly against a `FakeZendeskClient` (no real HTTP/respx needed there).
- **`client.py`** (`ZendeskClient`) is the only thing that talks to Zendesk. It owns three concerns at
  once: OAuth `client_credentials` token lifecycle (auto-fetch, early refresh ~30s before expiry, forced
  refresh + single retry on a live 401), 429 retry with exponential backoff / `Retry-After` (`MAX_ATTEMPTS
  = 3`), and a tiny whole-cache-invalidating TTL cache for GETs (`get`/`post`/`put` are the only entry
  points tools should use — `post`/`put` both call `invalidate_cache()` unconditionally). `_parse` is the
  single place HTTP status codes become `ZendeskAPIError` — never let a raw `httpx.Response` or exception
  leak past `ZendeskClient` into tool code.
- **`auth.py`** (`BearerAuthMiddleware`) protects the MCP server itself and is independent from Zendesk
  auth — it's a `{token: client_id}` map (inverted from `MCP_SERVER_API_KEYS`'s `{client_id: token}` shape
  in config.py) so each client's token can be revoked individually. Uses `hmac.compare_digest` for the
  token comparison; `exempt_paths` carves out the webhook route.
- **`webhooks.py`** is optional (only wired up if `ZENDESK_WEBHOOK_SECRET` is set): verifies Zendesk's HMAC
  webhook signature and calls `client.invalidate_cache()` on any event, independent of the bearer-token
  auth path (it's added to `exempt_paths` since Zendesk signs the payload itself instead).
- **`config.py`** centralizes env parsing (`load_settings`) and structured JSON logging
  (`configure_logging`, called once at import time in `server.py`) — log records are JSON with
  timestamp/level/logger/message, deliberately never including tokens or PII.
- Group filters accept either a name or a numeric ID: `tools/tickets.py` calls
  `tools/groups.resolve_group_id` to look up a name against `/groups.json`, so any group-aware feature
  should route through that same helper rather than re-implementing the lookup.
- Ticket listing/searching always goes through `/search.json` once any filter is present (plain
  `/tickets.json` only for the unfiltered case) — see `tools/tickets.list_tickets` for how filters are
  translated into Zendesk's structured query syntax (`status:`, `priority:`, `requester:`, `group:`).
- `add_comment(..., public=False)` creates an internal note; both the tool docstring and
  `tools/tickets.add_comment` flag that internal notes may carry sensitive internal context that shouldn't
  be surfaced unless the user explicitly asked for it — preserve that behavior in any related change.
- **`tools/guides.py`** exposes Help Center reads (`search_guides`, `get_guide`, `list_guide_categories`)
  and writes (`create_guide`, `update_guide`, `list_guide_permissions`) over the same
  OAuth-authenticated `ZendeskClient` — no separate auth path. Return payloads are deliberately lossy
  to keep context small: `search_guides` never returns the article body (only a ~280-char snippet),
  drops Zendesk metadata noise (labels, author, vote counts), deduplicates translations/near-duplicate
  results, and caps at `limit` (default 5); `get_guide` converts the HTML body to plain text via
  stdlib `html.parser` and truncates at `MAX_BODY_CHARS` (flagged via `truncated`). A 403 from
  `ZendeskClient` (`ZendeskAPIError.status == 403`) is caught in `get_guide`/`update_guide` and
  re-raised with a message naming the restricted article, instead of leaking the generic 401/403
  credentials message.
  - Three Help Center API traps that shape the write path — don't "simplify" past them:
    1. **Creating an article requires `permission_group_id`** (plus `locale`/`title`); there's no way
       around it, hence `list_guide_permissions` for discovery.
    2. **`PUT /help_center/articles/{id}` cannot change title/body.** Editing content goes through
       `PUT /help_center/articles/{id}/translations/{locale}` instead — `update_guide` uses that
       endpoint, not the article one.
    3. **Permission groups live under `/guide/permission_groups.json`** (not `/help_center/`), and
       only a Help Center manager can list them. `section`/`permission_group`/`visibility` in
       `create_guide` all accept a name *or* a numeric ID (via `guides._resolve_ref`) so a caller
       whose credentials can't list permission groups can still pass one by ID.
  - `create_guide`'s `draft: bool` and `visibility: str` have no default — same reasoning as
    `add_comment(..., public)` below: publishing an article is externally visible and hard to walk
    back, so the caller must decide explicitly rather than the tool assuming. `update_guide`'s `draft`
    *does* default to `None` (leave publication state alone) since there's a prior state to preserve
    on edit, unlike on create.

## Testing conventions

- `tests/test_tools.py` uses an in-file `FakeZendeskClient` (records `(method, path, kwargs)` calls,
  returns canned responses keyed by path) to test `tools/*.py` in isolation from real HTTP.
- `tests/test_client.py` uses `respx` to mock the real Zendesk HTTP endpoints (including the OAuth token
  endpoint) and a fake `_clock` callable injected into `ZendeskClient` to deterministically test TTL/token
  expiry without real sleeps.
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions don't need explicit
  `@pytest.mark.asyncio` markers to run, though the existing tests still add it explicitly — match existing
  style in whichever file you're editing.

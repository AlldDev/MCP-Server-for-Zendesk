# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MCP (Model Context Protocol) server exposing Zendesk Support operations as tools, meant to run remotely
over HTTP (not stdio) behind a reverse proxy, with per-client bearer tokens and IP allowlisting as
additional layers of defense. Full deployment/ops runbook, functional overview, and design rationale are
in `README.md` (Portuguese).

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
  refresh + single retry on a live 401 — the token lives in `self._token` and is attached per-request,
  never on `self._client.headers`, so the `/oauth/tokens` POST goes out with no `Authorization` header;
  Zendesk 401s the token endpoint if it carries the expired bearer token), retry with exponential backoff
  / `Retry-After` (`MAX_ATTEMPTS = 3`) on 429, 502/503/504, and network/transport errors
  (`httpx.TransportError`; a bare 500 is deliberately *not* retried — it can be a deterministic
  server-side bug rather than a transient blip), and a small TTL cache for GETs keyed on `(path, params)`
  (`get`/`post`/`put` are the only entry points tools should use — `post`/`put` call
  `invalidate_cache(path)`, which clears only the top-level resource segment via `_resource()`, so
  writing a ticket doesn't drop the Help Center cache; `invalidate_cache(None)` clears everything and is
  what `webhooks.py` uses). Several tools lean on that cache to keep repeated reference lookups cheap
  (`resolve_group_id`, `_field_titles`, the Help Center section lookups) — don't assume a
  redundant-looking GET is a redundant request. `_parse` is the single place HTTP status codes become
  `ZendeskAPIError` — never let a raw `httpx.Response` or exception leak past `ZendeskClient` into tool
  code; a network error exhausting retries is likewise wrapped into `ZendeskAPIError` in
  `_send_with_retry` rather than left to propagate as a raw `httpx` exception.
- **`auth.py`** (`BearerAuthMiddleware`) protects the MCP server itself and is independent from Zendesk
  auth — it's a `{token: client_id}` map (inverted from `MCP_SERVER_API_KEYS`'s `{client_id: token}` shape
  in config.py) so each client's token can be revoked individually. Uses `hmac.compare_digest` for the
  token comparison; `exempt_paths` carves out the webhook route. Two independent throttles, both keyed
  by a `{key: (count, timestamp)}` dict: `_failures` (by source IP, counts *failed* auth attempts,
  exponential backoff, always on) and `_client_requests` (by `client_id`, counts *successful* requests in
  a fixed window, opt-in via `client_rate_limit_max_requests` — `None` disables it, matching the
  cache/webhook pattern of off-unless-configured). The client-request limiter exists because every client
  shares the same Zendesk OAuth credentials/rate limit, so one misbehaving client can otherwise starve
  every other client of it.
- **`webhooks.py`** is optional (only wired up if `ZENDESK_WEBHOOK_SECRET` is set): verifies Zendesk's HMAC
  webhook signature and calls `client.invalidate_cache()` on any event, independent of the bearer-token
  auth path (it's added to `exempt_paths` since Zendesk signs the payload itself instead).
- **`config.py`** centralizes env parsing (`load_settings`) and structured JSON logging
  (`configure_logging`, called once at import time in `server.py`) — log records are JSON with
  timestamp/level/logger/message, deliberately never including tokens or PII.
- **`tools/fields.py`** is the shared response-shaping layer every other `tools/*.py` goes through, and
  the reason no tool ever returns a raw Zendesk object: the `*_FIELDS` whitelists plus
  `project`/`project_list` (strict allow-list intersection), `truncate` (word-boundary cut returning
  `(text, was_truncated)`), `enrich_ticket`/`build_name_maps` (attach sideloaded `*_name` fields),
  `offset_page_params`/`offset_next_cursor` (opaque page-number cursors for Search-style endpoints), and
  `paginate_all` (walks every page of an offset-paginated list, 20-page ceiling, returns
  `(items, truncated)`). Add a field to a whitelist rather than returning extra keys ad hoc, and reuse
  `paginate_all` instead of hand-rolling a page loop.
- Group filters accept either a name or a numeric ID: `tools/tickets.py` calls
  `tools/groups.resolve_group_id` to look up a name against `/groups.json`, so any group-aware feature
  should route through that same helper rather than re-implementing the lookup. `list_groups` paginates
  the full list on purpose — a first-page-only fetch made every group past the 100th unresolvable.
- Ticket listing/searching always goes through `/search.json` once any filter is present (plain
  `/tickets.json` only for the unfiltered case) — see `tools/tickets.list_tickets` for how filters are
  translated into Zendesk's structured query syntax (`status:`, `priority:`, `requester:`, `group:`).
- Read tools are shaped so the model can tell whether to narrow its request instead of paging blindly:
  every Search-backed tool returns `total_matches` (Zendesk's real hit count, `None` on the cursor-based
  `/tickets.json` path, which reports no total) next to `count` (how many came back), and any tool that
  can silently stop short reports `has_more`. Keep that invariant — a truncated list that looks complete
  is worse than a smaller one that says so.
- `get_ticket`/`create_ticket`/`update_ticket`/`add_comment` all return through
  `tickets._ticket_detail`, which resolves custom-field IDs to names via `_field_titles`
  (`/ticket_fields.json`) — but only when the ticket actually has custom fields set, so tickets without
  them cost no extra request. `_project_ticket_detail` also reprojects `satisfaction_rating` (already in
  `TICKET_DETAIL_FIELDS`) down to `SATISFACTION_FIELDS` (`score`, `comment`) and truncates a long comment
  — it isn't propagated to `list_tickets`/`search_tickets`'s summary shape (`TICKET_SUMMARY_FIELDS`
  doesn't include it), only the single-ticket detail path.
- `get_ticket_comments` defaults to `limit=20` and accepts `sort_order` (`"desc"` for the tail of a long
  thread); `get_ticket_audits` accepts `field_name` to return one field's history. Both exist so a long
  ticket doesn't have to be read in full to answer a narrow question — its `count` is post-filter and can
  be 0 with a non-null `next_cursor`. Comments carry an `attachments` list (via `ATTACHMENT_FIELDS`) when
  the raw comment has one — metadata only (filename/url/type/size), no upload support.
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
  - Resolving a `section_id`/`category_id` to a name is deliberately *not* done via `_taxonomy()` on
    the read path: that walks every page of both `/help_center/sections.json` and
    `/help_center/categories.json` (up to 40 requests) and is only for callers that need the whole
    tree — `list_guide_categories`, and `create_guide`'s name lookup. `search_guides` instead calls
    `_sections_by_id` *after* trimming to `limit`, fetching only the handful of sections that survived,
    and `get_guide` sideloads them in the article request itself
    (`?include=sections,categories`), falling back to `_fetch_ref` per ID if they don't come back.
    `_fetch_ref` swallows errors on purpose: a section we can't name shouldn't fail the article read.
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
    `add_comment(..., public)` above: publishing an article is externally visible and hard to walk
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

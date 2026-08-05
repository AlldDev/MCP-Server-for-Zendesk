from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from mcp_zendesk.auth import BearerAuthMiddleware
from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.config import configure_logging, load_settings
from mcp_zendesk.models import TicketPriority, TicketStatus
from mcp_zendesk.tools import groups, guides, search, tickets, users
from mcp_zendesk.webhooks import WEBHOOK_PATH, create_webhook_route

configure_logging()

settings = load_settings()
zendesk = ZendeskClient(
    subdomain=settings.zendesk_subdomain,
    client_id=settings.zendesk_oauth_client_id,
    client_secret=settings.zendesk_oauth_client_secret,
    scope=settings.zendesk_oauth_scope,
    cache_ttl_seconds=settings.cache_ttl_seconds,
)

mcp = MCPServer(
    "zendesk",
    instructions=(
        "Manage two distinct kinds of Zendesk content — don't conflate them. Support tickets are "
        "individual customer conversations/requests (list/get/create/update/comment). Help Center "
        "articles are public knowledge-base documentation (search/get/create/update, browsable via "
        "categories). A request about a customer's issue is a ticket; a request for a how-to or "
        "reference is a Help Center article."
    ),
)


@mcp.tool()
async def list_tickets(
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    requester_email: str | None = None,
    group: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """List Zendesk Support tickets (customer conversations/requests) — not Help Center articles;
    use list_guide_categories/search_guides to browse or find documentation instead. Optionally
    filtered by status, priority, requester email, or group (accepts a group name, e.g. "N1", or
    a numeric group ID). Each ticket includes a requester_name/assignee_name/group_name/
    organization_name when that Zendesk object is known. sort_by accepts "updated_at",
    "created_at", "priority", "status", or "ticket_type" (only applies when a filter is given);
    sort_order is "asc" or "desc". Returns up to limit tickets (default 25, keep it low); pass
    the previous call's next_cursor to fetch more. total_matches is how many tickets match in
    Zendesk (null when no filter is given) — if it is much larger than limit, narrow the filters
    instead of paging."""
    return await tickets.list_tickets(
        zendesk,
        status=status,
        priority=priority,
        requester_email=requester_email,
        group=group,
        sort_by=sort_by,
        sort_order=sort_order,
        cursor=cursor,
        limit=limit,
    )


@mcp.tool()
async def get_ticket(ticket_id: int) -> dict[str, Any]:
    """Get full details for a single Zendesk Support ticket (a customer conversation/request)
    by ID — not a Help Center article; use get_guide for that. Custom fields come back with the
    field's name alongside its id and value. When present, satisfaction_rating has score
    ("good"/"bad"/"offered"/"unoffered") and the customer's comment, if any."""
    return await tickets.get_ticket(zendesk, ticket_id)


@mcp.tool()
async def create_ticket(
    subject: str,
    comment_body: str,
    requester_email: str | None = None,
    priority: TicketPriority | None = None,
    tags: list[str] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new Zendesk Support ticket (a customer conversation/request) — not a Help Center
    article; use create_guide to publish documentation instead."""
    return await tickets.create_ticket(
        zendesk,
        subject=subject,
        comment_body=comment_body,
        requester_email=requester_email,
        priority=priority,
        tags=tags,
        custom_fields=custom_fields,
    )


@mcp.tool()
async def update_ticket(
    ticket_id: int,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    assignee_email: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update a Zendesk Support ticket's status, priority, assignee, or tags — not a Help Center
    article; use update_guide for that."""
    return await tickets.update_ticket(
        zendesk, ticket_id, status=status, priority=priority, assignee_email=assignee_email, tags=tags
    )


@mcp.tool()
async def add_comment(ticket_id: int, body: str, public: bool) -> dict[str, Any]:
    """Add a comment to a ticket. Specify public explicitly: True for a reply visible to the
    requester, False for an internal note — decide based on what the user asked, never default
    to one or the other. Internal notes may contain sensitive internal context; do not surface
    them unless the user explicitly asked for them."""
    return await tickets.add_comment(zendesk, ticket_id, body, public=public)


@mcp.tool()
async def get_ticket_comments(
    ticket_id: int, cursor: str | None = None, limit: int = 20, sort_order: str | None = None
) -> dict[str, Any]:
    """Get a ticket's comment thread, oldest first by default. Returns up to limit comments
    (default 20); pass the previous call's next_cursor to fetch more. To read only how a long
    thread ends, pass sort_order="desc" with a small limit instead of paging the whole thread.
    Comments with a file attached include an attachments list (filename, url, type, size)."""
    return await tickets.get_ticket_comments(
        zendesk, ticket_id, cursor=cursor, limit=limit, sort_order=sort_order
    )


@mcp.tool()
async def get_ticket_audits(
    ticket_id: int, cursor: str | None = None, limit: int = 50, field_name: str | None = None
) -> dict[str, Any]:
    """Get a ticket's change history (who changed what field and when). Comment-only audits
    are omitted; use get_ticket_comments for the conversation itself. Pass field_name (e.g.
    "status", "assignee_id", "priority") to get only that field's changes instead of the whole
    history. Returns up to limit audits (default 50) — count is after filtering, so it can be 0
    with a non-null next_cursor; pass the previous call's next_cursor to fetch more."""
    return await tickets.get_ticket_audits(
        zendesk, ticket_id, cursor=cursor, limit=limit, field_name=field_name
    )


@mcp.tool()
async def search_tickets(
    query: str,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search Zendesk Support tickets (customer support conversations/requests) by free text or
    structured query syntax (e.g. "status:open priority:high") — not Help Center articles or
    documentation; use search_guides for how-to/reference content. Each ticket includes a
    requester_name/assignee_name/group_name/organization_name when that Zendesk object is known.
    sort_by accepts "updated_at", "created_at", "priority", "status", or "ticket_type"; sort_order
    is "asc" or "desc". Returns up to limit results (default 25, keep it low); pass the previous
    call's next_cursor to fetch more. total_matches is how many tickets match the query in
    Zendesk — if it is much larger than limit, narrow the query instead of paging."""
    return await search.search_tickets(
        zendesk, query, sort_by=sort_by, sort_order=sort_order, cursor=cursor, limit=limit
    )


@mcp.tool()
async def get_user(user_id: int | None = None, email: str | None = None) -> dict[str, Any]:
    """Get a Zendesk user by ID or by email. Provide exactly one of user_id or email."""
    return await users.get_user(zendesk, user_id=user_id, email=email)


@mcp.tool()
async def list_organizations(
    name: str | None = None, cursor: str | None = None, limit: int = 25
) -> dict[str, Any]:
    """List organizations registered in Zendesk. Pass name to look one up by (partial) name
    instead of paging the whole account — prefer that whenever you already know who you're
    after. Otherwise returns up to limit organizations (default 25); pass the previous call's
    next_cursor to fetch more."""
    return await users.list_organizations(zendesk, name=name, cursor=cursor, limit=limit)


@mcp.tool()
async def list_groups() -> dict[str, Any]:
    """List support groups registered in Zendesk (use to look up a group's ID by name)."""
    return await groups.list_groups(zendesk)


@mcp.tool()
async def search_guides(
    query: str,
    limit: int = 5,
    page: int = 1,
    locale: str | None = None,
) -> dict[str, Any]:
    """Search Help Center articles (knowledge-base documentation/how-to content) by keyword —
    not support tickets; use search_tickets for a customer's actual conversation/request
    history. Returns a short snippet per article (never the full body) — call get_guide for an
    article whose snippet looks relevant. Results are deduplicated across translations and
    near-duplicate section/title matches, then capped at limit (default 5, keep it low).
    total_matches is how many articles match in Zendesk — if it is much larger than limit, use
    more specific keywords instead of paging. Help Center paging is offset-based: pass page=2
    when has_more is true."""
    return await guides.search_guides(zendesk, query, limit=limit, page=page, locale=locale)


@mcp.tool()
async def get_guide(article_id: int) -> dict[str, Any]:
    """Get the full content of a single Help Center article (knowledge-base documentation) by
    ID — not a support ticket; use get_ticket for that. HTML body is converted to readable text
    (truncated at ~8000 characters, flagged via truncated). If the article is restricted, raises
    a clear error naming the article instead of a generic credentials error."""
    return await guides.get_guide(zendesk, article_id)


@mcp.tool()
async def list_guide_categories() -> dict[str, Any]:
    """List Help Center categories (knowledge-base documentation, not support tickets — use
    list_tickets/search_tickets for actual customer conversations) with their sections nested
    inside, for exploratory navigation."""
    return await guides.list_guide_categories(zendesk)


@mcp.tool()
async def create_guide(
    section: str,
    title: str,
    body: str,
    permission_group: str,
    visibility: str,
    draft: bool,
    locale: str = "pt-br",
) -> dict[str, Any]:
    """Create a Help Center article (knowledge-base documentation) — not a support ticket; use
    create_ticket for a customer request instead. Specify draft explicitly: True creates an
    unpublished draft, False publishes it immediately to the Help Center — decide based on what
    the user asked, never default to one or the other. visibility is "everyone" for a publicly
    visible article, or a user segment name/ID to restrict it. section and permission_group
    accept a name or a numeric ID; call list_guide_permissions to discover valid values. body
    should be HTML; plain text is wrapped in paragraphs automatically."""
    return await guides.create_guide(
        zendesk,
        section,
        title,
        body,
        permission_group=permission_group,
        visibility=visibility,
        draft=draft,
        locale=locale,
    )


@mcp.tool()
async def update_guide(
    article_id: int,
    title: str | None = None,
    body: str | None = None,
    draft: bool | None = None,
    locale: str = "pt-br",
) -> dict[str, Any]:
    """Update an existing Help Center article's title, body, or draft status — not a support
    ticket; use update_ticket for that. Editing content goes through the article's translation
    for locale (Zendesk does not update title/body via the article endpoint directly). Provide
    at least one of title, body, or draft. draft has no effect on prior state when omitted,
    unlike create_guide's required draft."""
    return await guides.update_guide(zendesk, article_id, title=title, body=body, draft=draft, locale=locale)


@mcp.tool()
async def list_guide_permissions() -> dict[str, Any]:
    """List permission groups and user segments, for filling create_guide's permission_group
    and visibility parameters. "everyone" is also accepted as visibility without needing a
    segment from this list."""
    return await guides.list_guide_permissions(zendesk)


# DNS-rebinding Host-header check defaults to only 127.0.0.1/localhost with an explicit
# port, so it 421s every request through the reverse proxy; BearerAuthMiddleware already
# gates every request, so that check is redundant here.
#
# stateless_http: no MCP session is tracked across requests, so a redeploy never leaves
# a client holding a session ID the new process doesn't recognize. No tool here uses
# elicitation/sampling (the one capability stateless mode can't support), so nothing is lost.
http_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    stateless_http=True,
)
exempt_paths: frozenset[str] = frozenset()
if settings.zendesk_webhook_secret:
    http_app.routes.append(create_webhook_route(zendesk, settings.zendesk_webhook_secret))
    exempt_paths = frozenset({WEBHOOK_PATH})

app = BearerAuthMiddleware(
    http_app,
    api_keys=settings.api_keys,
    exempt_paths=exempt_paths,
    max_attempts=settings.auth_rate_limit_max_attempts,
    base_seconds=settings.auth_rate_limit_base_seconds,
    client_rate_limit_max_requests=settings.client_rate_limit_max_requests,
    client_rate_limit_window_seconds=settings.client_rate_limit_window_seconds,
)

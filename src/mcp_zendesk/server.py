from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from mcp_zendesk.auth import BearerAuthMiddleware
from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.config import configure_logging, load_settings
from mcp_zendesk.models import TicketPriority, TicketStatus
from mcp_zendesk.tools import groups, search, tickets, users
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

mcp = MCPServer("zendesk", instructions="Read and write Zendesk Support tickets.")


@mcp.tool()
async def list_tickets(
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    requester_email: str | None = None,
    group: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List Zendesk tickets, optionally filtered by status, priority, requester email, or group
    (accepts a group name, e.g. "N1", or a numeric group ID). Each ticket includes a
    requester_name/assignee_name/group_name/organization_name when that Zendesk object is known.
    sort_by accepts "updated_at", "created_at", "priority", "status", or "ticket_type" (only
    applies when a filter is given); sort_order is "asc" or "desc". Returns Zendesk's default
    page (up to 100 tickets); pass the previous call's next_cursor to fetch more."""
    return await tickets.list_tickets(
        zendesk,
        status=status,
        priority=priority,
        requester_email=requester_email,
        group=group,
        sort_by=sort_by,
        sort_order=sort_order,
        cursor=cursor,
    )


@mcp.tool()
async def get_ticket(ticket_id: int) -> dict[str, Any]:
    """Get full details for a single Zendesk ticket by ID."""
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
    """Create a new Zendesk ticket."""
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
    """Update a ticket's status, priority, assignee, or tags."""
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
async def get_ticket_comments(ticket_id: int, cursor: str | None = None) -> dict[str, Any]:
    """Get a ticket's comment thread in chronological order. Pass the previous call's
    next_cursor to fetch more."""
    return await tickets.get_ticket_comments(zendesk, ticket_id, cursor=cursor)


@mcp.tool()
async def get_ticket_audits(ticket_id: int, cursor: str | None = None) -> dict[str, Any]:
    """Get a ticket's change history (who changed what field and when). Comment-only audits
    are omitted; use get_ticket_comments for the conversation itself. Pass the previous call's
    next_cursor to fetch more."""
    return await tickets.get_ticket_audits(zendesk, ticket_id, cursor=cursor)


@mcp.tool()
async def search_tickets(
    query: str,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Search tickets by free text or Zendesk structured query syntax (e.g. "status:open priority:high").
    Each ticket includes a requester_name/assignee_name/group_name/organization_name when that
    Zendesk object is known. sort_by accepts "updated_at", "created_at", "priority", "status",
    or "ticket_type"; sort_order is "asc" or "desc". Returns Zendesk's default page (up to 100
    results); pass the previous call's next_cursor to fetch more."""
    return await search.search_tickets(zendesk, query, sort_by=sort_by, sort_order=sort_order, cursor=cursor)


@mcp.tool()
async def get_user(user_id: int | None = None, email: str | None = None) -> dict[str, Any]:
    """Get a Zendesk user by ID or by email. Provide exactly one of user_id or email."""
    return await users.get_user(zendesk, user_id=user_id, email=email)


@mcp.tool()
async def list_organizations() -> dict[str, Any]:
    """List organizations registered in Zendesk."""
    return await users.list_organizations(zendesk)


@mcp.tool()
async def list_groups() -> dict[str, Any]:
    """List support groups registered in Zendesk (use to look up a group's ID by name)."""
    return await groups.list_groups(zendesk)


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
)

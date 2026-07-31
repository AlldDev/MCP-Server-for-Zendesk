from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.models import TicketPriority, TicketStatus
from mcp_zendesk.tools.fields import (
    COMMENT_FIELDS,
    TICKET_DETAIL_FIELDS,
    build_name_maps,
    enrich_ticket,
    offset_next_cursor,
    offset_page_params,
    project,
    truncate,
)
from mcp_zendesk.tools.groups import resolve_group_id

DESCRIPTION_MAX_CHARS = 4000
COMMENT_BODY_MAX_CHARS = 2000
AUDIT_VALUE_MAX_CHARS = 500


def _project_ticket_detail(ticket: dict[str, Any]) -> dict[str, Any]:
    """Project a full ticket to TICKET_DETAIL_FIELDS, dropping unset custom fields and
    truncating a long description."""
    out = project(ticket, TICKET_DETAIL_FIELDS)
    if "custom_fields" in out:
        out["custom_fields"] = [f for f in out["custom_fields"] if f.get("value") is not None]
    if description := out.get("description"):
        body, truncated = truncate(description, DESCRIPTION_MAX_CHARS)
        out["description"] = body
        if truncated:
            out["description_truncated"] = True
    return out


async def list_tickets(
    client: ZendeskClient,
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
    filtered by status, priority, requester email, or group (accepts a group name or ID; names
    are resolved via the groups list). sort_by accepts "updated_at", "created_at", "priority",
    "status", or "ticket_type" (only applies when a filter is given); sort_order is "asc" or
    "desc". Returns up to limit tickets (default 25, keep it low); pass the previous call's
    next_cursor to fetch more."""
    if status or priority or requester_email or group:
        query_parts = ["type:ticket"]
        if status:
            query_parts.append(f"status:{status}")
        if priority:
            query_parts.append(f"priority:{priority}")
        if requester_email:
            query_parts.append(f"requester:{requester_email}")
        if group:
            group_id = int(group) if group.isdigit() else await resolve_group_id(client, group)
            query_parts.append(f"group:{group_id}")
        params: dict[str, Any] = {
            "include": "users,groups,organizations",
            "query": " ".join(query_parts),
            **offset_page_params(cursor, limit),
        }
        if sort_by:
            params["sort_by"] = sort_by
        if sort_order:
            params["sort_order"] = sort_order
        data = await client.get("/search.json", params=params)
        tickets = data.get("results", [])
        next_cursor = offset_next_cursor(cursor, bool(data.get("next_page")))
    else:
        params = {"include": "users,groups,organizations", "page[size]": max(1, min(limit, 100))}
        if cursor:
            params["page[after]"] = cursor
        data = await client.get("/tickets.json", params=params)
        tickets = data.get("tickets", [])
        meta = data.get("meta", {})
        next_cursor = meta.get("after_cursor") if meta.get("has_more") else None
    names = build_name_maps(data)
    return {
        "count": len(tickets),
        "tickets": [enrich_ticket(t, names) for t in tickets],
        "next_cursor": next_cursor,
    }


async def get_ticket(client: ZendeskClient, ticket_id: int) -> dict[str, Any]:
    """Get full details for a single Zendesk Support ticket (a customer conversation/request)
    by ID — not a Help Center article; use get_guide for that."""
    data = await client.get(f"/tickets/{ticket_id}.json")
    return _project_ticket_detail(data["ticket"])


async def create_ticket(
    client: ZendeskClient,
    subject: str,
    comment_body: str,
    requester_email: str | None = None,
    priority: TicketPriority | None = None,
    tags: list[str] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new Zendesk Support ticket (a customer conversation/request) — not a Help Center
    article; use create_guide to publish documentation instead.

    custom_fields, if given, is a list of {"id": <field_id>, "value": <value>}
    (Zendesk's own format), since custom fields vary per account (spec section 11).
    """
    ticket: dict[str, Any] = {"subject": subject, "comment": {"body": comment_body}}
    if requester_email:
        ticket["requester"] = {"email": requester_email}
    if priority:
        ticket["priority"] = priority
    if tags:
        ticket["tags"] = tags
    if custom_fields:
        ticket["custom_fields"] = custom_fields
    data = await client.post("/tickets.json", json={"ticket": ticket})
    return _project_ticket_detail(data["ticket"])


async def update_ticket(
    client: ZendeskClient,
    ticket_id: int,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    assignee_email: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update a Zendesk Support ticket's status, priority, assignee, or tags — not a Help Center
    article; use update_guide for that."""
    fields: dict[str, Any] = {}
    if status:
        fields["status"] = status
    if priority:
        fields["priority"] = priority
    if assignee_email:
        fields["assignee_email"] = assignee_email
    if tags is not None:
        fields["tags"] = tags
    data = await client.put(f"/tickets/{ticket_id}.json", json={"ticket": fields})
    return _project_ticket_detail(data["ticket"])


async def add_comment(
    client: ZendeskClient,
    ticket_id: int,
    body: str,
    public: bool,
) -> dict[str, Any]:
    """Add a comment to a ticket. Specify public explicitly: True for a reply visible to the
    requester, False for an internal note (spec section 11: handle with care) — decide based
    on what the user asked, never default to one or the other."""
    data = await client.put(
        f"/tickets/{ticket_id}.json",
        json={"ticket": {"comment": {"body": body, "public": public}}},
    )
    return _project_ticket_detail(data["ticket"])


def _project_comment(comment: dict[str, Any]) -> dict[str, Any]:
    out = project(comment, COMMENT_FIELDS)
    if body := out.get("body"):
        text, truncated = truncate(body, COMMENT_BODY_MAX_CHARS)
        out["body"] = text
        if truncated:
            out["truncated"] = True
    return out


async def get_ticket_comments(
    client: ZendeskClient, ticket_id: int, cursor: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Get a ticket's comment thread in chronological order. Returns up to limit comments
    (default 50); pass the previous call's next_cursor to fetch more."""
    params: dict[str, Any] = {"page[size]": max(1, min(limit, 100))}
    if cursor:
        params["page[after]"] = cursor
    data = await client.get(f"/tickets/{ticket_id}/comments.json", params=params)
    comments = data.get("comments", [])
    meta = data.get("meta", {})
    return {
        "count": len(comments),
        "comments": [_project_comment(c) for c in comments],
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }


def _change_events(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in audit.get("events", []) if e.get("type") == "Change"]


def _truncate_audit_value(value: Any) -> Any:
    if isinstance(value, str):
        text, _ = truncate(value, AUDIT_VALUE_MAX_CHARS)
        return text
    return value


def _simplify_audit(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": audit["id"],
        "author_id": audit.get("author_id"),
        "created_at": audit.get("created_at"),
        "changes": [
            {
                "field_name": e.get("field_name"),
                "previous_value": _truncate_audit_value(e.get("previous_value")),
                "value": _truncate_audit_value(e.get("value")),
            }
            for e in _change_events(audit)
        ],
    }


async def get_ticket_audits(
    client: ZendeskClient, ticket_id: int, cursor: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Get a ticket's change history (who changed what field and when). Comment-only audits
    are omitted; use get_ticket_comments for the conversation itself. Returns up to limit
    audits (default 50); pass the previous call's next_cursor to fetch more."""
    params: dict[str, Any] = {"page[size]": max(1, min(limit, 100))}
    if cursor:
        params["page[after]"] = cursor
    data = await client.get(f"/tickets/{ticket_id}/audits.json", params=params)
    audits = [a for a in data.get("audits", []) if _change_events(a)]
    meta = data.get("meta", {})
    return {
        "count": len(audits),
        "audits": [_simplify_audit(a) for a in audits],
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }

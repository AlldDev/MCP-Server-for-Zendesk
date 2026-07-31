from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.models import TicketPriority, TicketStatus
from mcp_zendesk.tools.fields import (
    COMMENT_FIELDS,
    TICKET_DETAIL_FIELDS,
    build_name_maps,
    enrich_ticket,
    project,
    project_list,
)
from mcp_zendesk.tools.groups import resolve_group_id


async def list_tickets(
    client: ZendeskClient,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    requester_email: str | None = None,
    group: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """List Zendesk tickets, optionally filtered by status, priority, requester email, or group
    (accepts a group name or ID; names are resolved via the groups list). sort_by accepts
    "updated_at", "created_at", "priority", "status", or "ticket_type" (only applies when a
    filter is given); sort_order is "asc" or "desc". Returns Zendesk's default page (up to
    100 tickets); pass the previous call's next_cursor to fetch more."""
    params: dict[str, Any] = {"include": "users,groups,organizations"}
    if cursor:
        params["page[after]"] = cursor
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
        params["query"] = " ".join(query_parts)
        if sort_by:
            params["sort_by"] = sort_by
        if sort_order:
            params["sort_order"] = sort_order
        data = await client.get("/search.json", params=params)
        tickets = data.get("results", [])
    else:
        data = await client.get("/tickets.json", params=params)
        tickets = data.get("tickets", [])
    names = build_name_maps(data)
    meta = data.get("meta", {})
    return {
        "count": len(tickets),
        "tickets": [enrich_ticket(t, names) for t in tickets],
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }


async def get_ticket(client: ZendeskClient, ticket_id: int) -> dict[str, Any]:
    """Get full details for a single Zendesk ticket by ID."""
    data = await client.get(f"/tickets/{ticket_id}.json")
    return project(data["ticket"], TICKET_DETAIL_FIELDS)


async def create_ticket(
    client: ZendeskClient,
    subject: str,
    comment_body: str,
    requester_email: str | None = None,
    priority: TicketPriority | None = None,
    tags: list[str] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new Zendesk ticket.

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
    return project(data["ticket"], TICKET_DETAIL_FIELDS)


async def update_ticket(
    client: ZendeskClient,
    ticket_id: int,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    assignee_email: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update a ticket's status, priority, assignee, or tags."""
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
    return project(data["ticket"], TICKET_DETAIL_FIELDS)


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
    return project(data["ticket"], TICKET_DETAIL_FIELDS)


async def get_ticket_comments(
    client: ZendeskClient, ticket_id: int, cursor: str | None = None
) -> dict[str, Any]:
    """Get a ticket's comment thread in chronological order. Pass the previous call's
    next_cursor to fetch more."""
    params: dict[str, Any] = {"page[size]": 100}
    if cursor:
        params["page[after]"] = cursor
    data = await client.get(f"/tickets/{ticket_id}/comments.json", params=params)
    comments = data.get("comments", [])
    meta = data.get("meta", {})
    return {
        "count": len(comments),
        "comments": project_list(comments, COMMENT_FIELDS),
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }


def _change_events(audit: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in audit.get("events", []) if e.get("type") == "Change"]


def _simplify_audit(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": audit["id"],
        "author_id": audit.get("author_id"),
        "created_at": audit.get("created_at"),
        "changes": [
            {"field_name": e.get("field_name"), "previous_value": e.get("previous_value"), "value": e.get("value")}
            for e in _change_events(audit)
        ],
    }


async def get_ticket_audits(
    client: ZendeskClient, ticket_id: int, cursor: str | None = None
) -> dict[str, Any]:
    """Get a ticket's change history (who changed what field and when). Comment-only audits
    are omitted; use get_ticket_comments for the conversation itself. Pass the previous call's
    next_cursor to fetch more."""
    params: dict[str, Any] = {"page[size]": 100}
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

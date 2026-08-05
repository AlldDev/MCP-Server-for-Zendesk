from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.models import TicketPriority, TicketStatus
from mcp_zendesk.tools.fields import (
    ATTACHMENT_FIELDS,
    COMMENT_FIELDS,
    SATISFACTION_FIELDS,
    TICKET_DETAIL_FIELDS,
    build_name_maps,
    enrich_ticket,
    offset_next_cursor,
    offset_page_params,
    paginate_all,
    project,
    project_list,
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
    if isinstance(rating := out.get("satisfaction_rating"), dict):
        rating = project(rating, SATISFACTION_FIELDS)
        if comment := rating.get("comment"):
            rating["comment"], _truncated = truncate(comment, COMMENT_BODY_MAX_CHARS)
        out["satisfaction_rating"] = rating
    return out


async def _field_titles(client: ZendeskClient) -> dict[int, str]:
    """id->title map for the account's ticket fields."""
    ticket_fields, _truncated = await paginate_all(client, "/ticket_fields.json", "ticket_fields")
    return {f["id"]: f.get("title") for f in ticket_fields}


async def _ticket_detail(client: ZendeskClient, ticket: dict[str, Any]) -> dict[str, Any]:
    """_project_ticket_detail plus a name on each custom field — a bare {"id": 360012345,
    "value": ...} is unreadable, so the model can't tell which fields matter. Only fetches
    /ticket_fields.json when the ticket actually has custom fields set, and that GET is cached
    by ZendeskClient, so it costs ~one real call per cache window rather than one per ticket."""
    out = _project_ticket_detail(ticket)
    if custom_fields := out.get("custom_fields"):
        titles = await _field_titles(client)
        out["custom_fields"] = [
            {"id": f["id"], "name": titles.get(f["id"]), "value": f["value"]} for f in custom_fields
        ]
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
    next_cursor to fetch more. total_matches is how many tickets match in Zendesk (null when
    no filter is given) — if it is much larger than limit, narrow the filters instead of
    paging."""
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
        # Search reports the full number of hits; plain cursor listing does not.
        total_matches = data.get("count")
    else:
        params = {"include": "users,groups,organizations", "page[size]": max(1, min(limit, 100))}
        if cursor:
            params["page[after]"] = cursor
        data = await client.get("/tickets.json", params=params)
        tickets = data.get("tickets", [])
        meta = data.get("meta", {})
        next_cursor = meta.get("after_cursor") if meta.get("has_more") else None
        total_matches = None
    names = build_name_maps(data)
    return {
        "count": len(tickets),
        "total_matches": total_matches,
        "tickets": [enrich_ticket(t, names) for t in tickets],
        "next_cursor": next_cursor,
    }


async def get_ticket(client: ZendeskClient, ticket_id: int) -> dict[str, Any]:
    """Get full details for a single Zendesk Support ticket (a customer conversation/request)
    by ID — not a Help Center article; use get_guide for that. Custom fields come back with
    the field's name alongside its id and value. When present, satisfaction_rating has score
    ("good"/"bad"/"offered"/"unoffered") and the customer's comment, if any."""
    data = await client.get(f"/tickets/{ticket_id}.json")
    return await _ticket_detail(client, data["ticket"])


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
    (Zendesk's own format), since custom fields vary per account. get_ticket reports each custom
    field's name alongside its id, which is how you find the id to write to.
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
    return await _ticket_detail(client, data["ticket"])


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
    return await _ticket_detail(client, data["ticket"])


async def add_comment(
    client: ZendeskClient,
    ticket_id: int,
    body: str,
    public: bool,
) -> dict[str, Any]:
    """Add a comment to a ticket. Specify public explicitly: True for a reply visible to the
    requester, False for an internal note (handle with care) — decide based
    on what the user asked, never default to one or the other."""
    data = await client.put(
        f"/tickets/{ticket_id}.json",
        json={"ticket": {"comment": {"body": body, "public": public}}},
    )
    return await _ticket_detail(client, data["ticket"])


def _project_comment(comment: dict[str, Any]) -> dict[str, Any]:
    out = project(comment, COMMENT_FIELDS)
    if body := out.get("body"):
        text, truncated = truncate(body, COMMENT_BODY_MAX_CHARS)
        out["body"] = text
        if truncated:
            out["truncated"] = True
    if attachments := out.get("attachments"):
        out["attachments"] = project_list(attachments, ATTACHMENT_FIELDS)
    return out


async def get_ticket_comments(
    client: ZendeskClient,
    ticket_id: int,
    cursor: str | None = None,
    limit: int = 20,
    sort_order: str | None = None,
) -> dict[str, Any]:
    """Get a ticket's comment thread, oldest first by default. Returns up to limit comments
    (default 20); pass the previous call's next_cursor to fetch more. To read only how a long
    thread ends, pass sort_order="desc" with a small limit instead of paging the whole thread.
    Comments with a file attached include an attachments list (filename, url, type, size)."""
    params: dict[str, Any] = {"page[size]": max(1, min(limit, 100))}
    if cursor:
        params["page[after]"] = cursor
    if sort_order:
        params["sort_order"] = sort_order
    data = await client.get(f"/tickets/{ticket_id}/comments.json", params=params)
    comments = data.get("comments", [])
    meta = data.get("meta", {})
    return {
        "count": len(comments),
        "comments": [_project_comment(c) for c in comments],
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }


def _change_events(audit: dict[str, Any], field_name: str | None = None) -> list[dict[str, Any]]:
    events = [e for e in audit.get("events", []) if e.get("type") == "Change"]
    if field_name:
        events = [e for e in events if e.get("field_name") == field_name]
    return events


def _truncate_audit_value(value: Any) -> Any:
    if isinstance(value, str):
        text, _ = truncate(value, AUDIT_VALUE_MAX_CHARS)
        return text
    return value


def _simplify_audit(audit: dict[str, Any], field_name: str | None = None) -> dict[str, Any]:
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
            for e in _change_events(audit, field_name)
        ],
    }


async def get_ticket_audits(
    client: ZendeskClient,
    ticket_id: int,
    cursor: str | None = None,
    limit: int = 50,
    field_name: str | None = None,
) -> dict[str, Any]:
    """Get a ticket's change history (who changed what field and when). Comment-only audits
    are omitted; use get_ticket_comments for the conversation itself. Pass field_name (e.g.
    "status", "assignee_id", "priority") to get only that field's changes instead of the whole
    history. Returns up to limit audits (default 50) — count is after filtering, so it can be
    0 with a non-null next_cursor; pass the previous call's next_cursor to fetch more."""
    params: dict[str, Any] = {"page[size]": max(1, min(limit, 100))}
    if cursor:
        params["page[after]"] = cursor
    data = await client.get(f"/tickets/{ticket_id}/audits.json", params=params)
    audits = [a for a in data.get("audits", []) if _change_events(a, field_name)]
    meta = data.get("meta", {})
    return {
        "count": len(audits),
        "audits": [_simplify_audit(a, field_name) for a in audits],
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }

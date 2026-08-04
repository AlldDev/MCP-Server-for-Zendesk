from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.tools.fields import build_name_maps, enrich_ticket, offset_next_cursor, offset_page_params


async def search_tickets(
    client: ZendeskClient,
    query: str,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search Zendesk Support tickets (customer support conversations/requests) by free text or
    structured query syntax (e.g. "status:open priority:high") — not Help Center articles or
    documentation; use search_guides for how-to/reference content. sort_by accepts "updated_at",
    "created_at", "priority", "status", or "ticket_type"; sort_order is "asc" or "desc". Returns
    up to limit results (default 25, keep it low); pass the previous call's next_cursor to fetch
    more. total_matches is how many tickets match the query in Zendesk — if it is much larger
    than limit, narrow the query instead of paging."""
    scoped_query = query if "type:" in query else f"type:ticket {query}"
    params: dict[str, Any] = {
        "query": scoped_query,
        "include": "users,groups,organizations",
        **offset_page_params(cursor, limit),
    }
    if sort_by:
        params["sort_by"] = sort_by
    if sort_order:
        params["sort_order"] = sort_order
    data = await client.get("/search.json", params=params)
    results = data.get("results", [])
    names = build_name_maps(data)
    return {
        "count": len(results),
        "total_matches": data.get("count"),
        "tickets": [enrich_ticket(t, names) for t in results],
        "next_cursor": offset_next_cursor(cursor, bool(data.get("next_page"))),
    }

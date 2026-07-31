from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.tools.fields import build_name_maps, enrich_ticket


async def search_tickets(
    client: ZendeskClient,
    query: str,
    sort_by: str | None = None,
    sort_order: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search tickets by free text or Zendesk structured query syntax (e.g. "status:open priority:high").
    sort_by accepts "updated_at", "created_at", "priority", "status", or "ticket_type"; sort_order
    is "asc" or "desc". Returns up to limit results (default 25, keep it low); pass the
    previous call's next_cursor to fetch more."""
    scoped_query = query if "type:" in query else f"type:ticket {query}"
    params: dict[str, Any] = {
        "query": scoped_query,
        "include": "users,groups,organizations",
        "page[size]": max(1, min(limit, 100)),
    }
    if sort_by:
        params["sort_by"] = sort_by
    if sort_order:
        params["sort_order"] = sort_order
    if cursor:
        params["page[after]"] = cursor
    data = await client.get("/search.json", params=params)
    results = data.get("results", [])
    names = build_name_maps(data)
    meta = data.get("meta", {})
    return {
        "count": len(results),
        "tickets": [enrich_ticket(t, names) for t in results],
        "next_cursor": meta.get("after_cursor") if meta.get("has_more") else None,
    }

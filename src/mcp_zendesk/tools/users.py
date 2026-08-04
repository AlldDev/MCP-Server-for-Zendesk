from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.tools.fields import (
    ORGANIZATION_FIELDS,
    USER_FIELDS,
    offset_next_cursor,
    offset_page_params,
    project,
    project_list,
)


async def get_user(client: ZendeskClient, user_id: int | None = None, email: str | None = None) -> dict[str, Any]:
    """Get a Zendesk user by ID or by email. Provide exactly one of user_id or email."""
    if bool(user_id) == bool(email):
        raise ValueError("Provide exactly one of user_id or email.")
    if user_id:
        data = await client.get(f"/users/{user_id}.json")
        return project(data["user"], USER_FIELDS)
    data = await client.get("/users/search.json", params={"query": email})
    users = data.get("users", [])
    if not users:
        raise ValueError(f"No Zendesk user found for email {email!r}.")
    return project(users[0], USER_FIELDS)


async def list_organizations(
    client: ZendeskClient, name: str | None = None, cursor: str | None = None, limit: int = 25
) -> dict[str, Any]:
    """List organizations registered in Zendesk. Pass name to look one up by (partial) name
    instead of paging the whole account — prefer that whenever you already know who you're
    after. Otherwise returns up to limit organizations (default 25); pass the previous call's
    next_cursor to fetch more."""
    if name:
        data = await client.get("/organizations/autocomplete.json", params={"name": name})
        organizations = data.get("organizations", [])
        next_cursor = None
    else:
        data = await client.get("/organizations.json", params=offset_page_params(cursor, limit))
        organizations = data.get("organizations", [])
        next_cursor = offset_next_cursor(cursor, bool(data.get("next_page")))
    return {
        "count": len(organizations),
        "organizations": project_list(organizations, ORGANIZATION_FIELDS),
        "next_cursor": next_cursor,
    }

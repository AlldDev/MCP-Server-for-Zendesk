from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.tools.fields import ORGANIZATION_FIELDS, USER_FIELDS, project, project_list


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


async def list_organizations(client: ZendeskClient) -> dict[str, Any]:
    """List organizations registered in Zendesk."""
    data = await client.get("/organizations.json")
    organizations = data.get("organizations", [])
    return {"count": len(organizations), "organizations": project_list(organizations, ORGANIZATION_FIELDS)}

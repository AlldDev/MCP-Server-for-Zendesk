from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.tools.fields import GROUP_FIELDS, project_list


async def list_groups(client: ZendeskClient) -> dict[str, Any]:
    """List support groups registered in Zendesk."""
    data = await client.get("/groups.json")
    groups = data.get("groups", [])
    return {"count": len(groups), "groups": project_list(groups, GROUP_FIELDS)}


async def resolve_group_id(client: ZendeskClient, name: str) -> int:
    """Resolve a group name to its ID (case-insensitive exact match)."""
    data = await list_groups(client)
    for group in data["groups"]:
        if group["name"].strip().lower() == name.strip().lower():
            return group["id"]
    raise ValueError(f"No Zendesk group found named {name!r}.")

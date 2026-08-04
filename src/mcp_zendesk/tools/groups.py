from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient
from mcp_zendesk.tools.fields import GROUP_FIELDS, paginate_all, project_list


async def list_groups(client: ZendeskClient) -> dict[str, Any]:
    """List support groups registered in Zendesk. Paginated through in full — a group has only
    three fields, and resolve_group_id needs the whole list to match a name."""
    groups, truncated = await paginate_all(client, "/groups.json", "groups")
    return {"count": len(groups), "groups": project_list(groups, GROUP_FIELDS), "has_more": truncated}


async def resolve_group_id(client: ZendeskClient, name: str) -> int:
    """Resolve a group name to its ID (case-insensitive exact match)."""
    data = await list_groups(client)
    for group in data["groups"]:
        if group["name"].strip().lower() == name.strip().lower():
            return group["id"]
    raise ValueError(f"No Zendesk group found named {name!r}.")

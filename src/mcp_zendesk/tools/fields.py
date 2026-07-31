from __future__ import annotations

from typing import Any

TICKET_SUMMARY_FIELDS = {
    "id", "subject", "status", "priority",
    "requester_id", "assignee_id", "group_id", "organization_id",
    "tags", "created_at", "updated_at",
}
TICKET_DETAIL_FIELDS = TICKET_SUMMARY_FIELDS | {
    "description", "custom_fields", "satisfaction_rating", "due_at", "submitter_id", "type",
}
USER_FIELDS = {"id", "name", "email", "role", "organization_id", "tags", "created_at", "updated_at"}
ORGANIZATION_FIELDS = {"id", "name", "domain_names", "tags", "created_at", "updated_at"}
GROUP_FIELDS = {"id", "name", "default"}
COMMENT_FIELDS = {"id", "author_id", "body", "public", "created_at"}


def project(obj: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    """Trim a raw Zendesk API object down to the fields useful to an assistant."""
    return {k: v for k, v in obj.items() if k in fields}


def project_list(objs: list[dict[str, Any]], fields: set[str]) -> list[dict[str, Any]]:
    return [project(o, fields) for o in objs]


def build_name_maps(data: dict[str, Any]) -> dict[str, dict[int, str]]:
    """Build id->name lookups from a Zendesk response sideloaded via include=users,groups,organizations."""
    return {
        "users": {u["id"]: u.get("name") for u in data.get("users", [])},
        "groups": {g["id"]: g.get("name") for g in data.get("groups", [])},
        "organizations": {o["id"]: o.get("name") for o in data.get("organizations", [])},
    }


def enrich_ticket(ticket: dict[str, Any], names: dict[str, dict[int, str]]) -> dict[str, Any]:
    """Project a ticket to summary fields and attach *_name fields from sideloaded objects."""
    out = project(ticket, TICKET_SUMMARY_FIELDS)
    if (rid := ticket.get("requester_id")) in names["users"]:
        out["requester_name"] = names["users"][rid]
    if (aid := ticket.get("assignee_id")) in names["users"]:
        out["assignee_name"] = names["users"][aid]
    if (gid := ticket.get("group_id")) in names["groups"]:
        out["group_name"] = names["groups"][gid]
    if (oid := ticket.get("organization_id")) in names["organizations"]:
        out["organization_name"] = names["organizations"][oid]
    return out

from __future__ import annotations

from typing import Any

from mcp_zendesk.client import ZendeskClient

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
COMMENT_FIELDS = {"id", "author_id", "body", "public", "created_at", "attachments"}
ATTACHMENT_FIELDS = {"id", "file_name", "content_url", "content_type", "size"}
SATISFACTION_FIELDS = {"score", "comment"}
ARTICLE_DETAIL_FIELDS = {"id", "title", "html_url", "section_id", "updated_at", "locale"}
ARTICLE_WRITE_FIELDS = ARTICLE_DETAIL_FIELDS | {"draft", "permission_group_id", "user_segment_id"}
TRANSLATION_FIELDS = {"id", "title", "locale", "draft", "updated_at"}
GUIDE_REF_FIELDS = {"id", "name"}


def project(obj: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    """Trim a raw Zendesk API object down to the fields useful to an assistant."""
    return {k: v for k, v in obj.items() if k in fields}


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Cut text at a word boundary near max_chars, appending an ellipsis if trimmed."""
    if len(text) <= max_chars:
        return text, False
    cut = text.rfind(" ", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    return text[:cut].rstrip() + "…", True


def project_list(objs: list[dict[str, Any]], fields: set[str]) -> list[dict[str, Any]]:
    return [project(o, fields) for o in objs]


def offset_page_params(cursor: str | None, limit: int) -> dict[str, Any]:
    """Classic offset-pagination params (page/per_page) for a Search-style Zendesk endpoint,
    from an opaque page-number cursor."""
    params: dict[str, Any] = {"per_page": max(1, min(limit, 100))}
    if cursor:
        try:
            params["page"] = int(cursor)
        except ValueError:
            raise ValueError(f"Invalid cursor {cursor!r}; pass the next_cursor from a previous call.") from None
    return params


def offset_next_cursor(cursor: str | None, has_more: bool) -> str | None:
    """Advance an opaque page-number cursor when the endpoint reports another page."""
    if not has_more:
        return None
    return str((int(cursor) if cursor else 1) + 1)


async def paginate_all(
    client: ZendeskClient, path: str, list_key: str, max_pages: int = 20
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch every page of an offset-paginated Zendesk list endpoint, up to max_pages.
    Returns (items, truncated) — truncated is True only if max_pages was hit before next_page
    went null. For small reference lists (groups, permission groups, ticket fields, Help Center
    taxonomy) where a silent first-page cut would make the model believe it saw everything.

    ponytail: 20-page (2000-item) ceiling, not unbounded; raise max_pages if a list ever gets
    meaningfully bigger than that.
    """
    items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        data = await client.get(path, params={"per_page": 100, "page": page})
        items.extend(data.get(list_key, []))
        if not data.get("next_page"):
            return items, False
    return items, True


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

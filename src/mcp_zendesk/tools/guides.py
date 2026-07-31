from __future__ import annotations

import asyncio
import html
import re
from html.parser import HTMLParser
from typing import Any

from mcp_zendesk.client import ZendeskAPIError, ZendeskClient
from mcp_zendesk.tools.fields import (
    ARTICLE_DETAIL_FIELDS,
    ARTICLE_WRITE_FIELDS,
    GUIDE_REF_FIELDS,
    TRANSLATION_FIELDS,
    project,
    project_list,
    truncate,
)

SNIPPET_CHARS = 280
MAX_BODY_CHARS = 8000

_TAG_RE = re.compile(r"<[a-z][^>]*>", re.I)

_SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "blockquote", "pre"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# ponytail: line heuristic for theme chrome authors sometimes paste into the body (helpful-vote
# widgets, back-to-top links); the article body itself never legitimately starts a line this way.
_BOILERPLATE = re.compile(
    r"^(this article (was|is) helpful|este artigo foi útil|was this article helpful"
    r"|\d+ (out )?of \d+ found this helpful|\d+ de \d+ acharam"
    r"|voltar ao topo|back to top)\b",
    re.I,
)


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth:
            return
        elif tag in _HEADING_TAGS:
            self._parts.append("\n\n## ")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            return
        if tag in _HEADING_TAGS:
            self._parts.append("\n\n## ")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _html_to_text(html: str) -> str:
    """Strip an article's HTML body down to readable text: headings become `## `, list items
    become `- `, scripts/styles/decorative markup are dropped, and helpful-vote/back-to-top
    chrome is filtered line by line."""
    parser = _HTMLToText()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", parser.text())
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line for line in text.split("\n") if not _BOILERPLATE.match(line.strip())]
    return "\n".join(lines).strip()


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


async def _taxonomy(client: ZendeskClient) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], bool]:
    """Fetch id->object maps for sections and categories (first page only), for resolving
    section_id/category_id to human-readable names.

    ponytail: first page only (100 sections/categories); above that a name resolves to None
    and the caller's has_more reflects the cutoff — paginate if a Help Center ever exceeds this.
    """
    sections_data, categories_data = await asyncio.gather(
        client.get("/help_center/sections.json", params={"per_page": 100}),
        client.get("/help_center/categories.json", params={"per_page": 100}),
    )
    sections = {s["id"]: s for s in sections_data.get("sections", [])}
    categories = {c["id"]: c for c in categories_data.get("categories", [])}
    truncated = bool(sections_data.get("next_page")) or bool(categories_data.get("next_page"))
    return sections, categories, truncated


def _snippet(article: dict[str, Any]) -> str:
    raw_snippet = article.get("snippet")
    if raw_snippet:
        text = _html_to_text(re.sub(r"</?em>", "", raw_snippet))
    else:
        text = _html_to_text(article.get("body") or "")
    clean, _ = truncate(text, SNIPPET_CHARS)
    return clean


def _text_to_html(body: str) -> str:
    """Pass HTML through untouched; wrap tag-free input as escaped <p> paragraphs.

    ponytail: tag sniff, not a parser. Markdown in the body renders literally — the docstring
    says to send HTML; add a markdown subset converter only if that turns out to bite.
    """
    if _TAG_RE.search(body):
        return body
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def _resolve_ref(items: list[dict[str, Any]], value: str, kind: str) -> int:
    """Resolve a name (case-insensitive exact match) or numeric ID to an ID."""
    if value.isdigit():
        return int(value)
    matches = [item for item in items if (item.get("name") or "").strip().lower() == value.strip().lower()]
    if not matches:
        raise ValueError(f"No {kind} found named {value!r}.")
    if len(matches) > 1:
        raise ValueError(f"Multiple {kind}s named {value!r} found; pass the numeric ID instead.")
    return matches[0]["id"]


async def search_guides(
    client: ZendeskClient,
    query: str,
    limit: int = 5,
    page: int = 1,
    locale: str | None = None,
) -> dict[str, Any]:
    """Search Help Center articles by keyword. Returns a short snippet per article (never the
    full body) — call get_guide for an article whose snippet looks relevant. Results are
    deduplicated across translations and near-duplicate section/title matches, then capped at
    limit (default 5, keep it low). Help Center paging is offset-based: pass page=2 when
    has_more is true."""
    per_page = min(max(limit * 3, 10), 100)
    params: dict[str, Any] = {"query": query, "per_page": per_page, "page": page}
    if locale:
        params["locale"] = locale
    data = await client.get("/help_center/articles/search.json", params=params)
    results = data.get("results", [])
    sections, _categories, _truncated_taxonomy = await _taxonomy(client)

    by_id: dict[int, dict[str, Any]] = {}
    for index, article in enumerate(results):
        article_id = article["id"]
        entry = {"article": article, "index": index}
        existing = by_id.get(article_id)
        if existing is None:
            by_id[article_id] = entry
            continue
        if locale and article.get("locale") == locale and existing["article"].get("locale") != locale:
            by_id[article_id] = entry
        elif not locale and (article.get("edited_at") or "") > (existing["article"].get("edited_at") or ""):
            by_id[article_id] = entry

    by_section_title: dict[tuple[Any, str], dict[str, Any]] = {}
    for entry in by_id.values():
        article = entry["article"]
        key = (article.get("section_id"), _normalize_title(article.get("title") or ""))
        existing = by_section_title.get(key)
        if existing is None or (article.get("edited_at") or "") > (existing["article"].get("edited_at") or ""):
            by_section_title[key] = entry

    query_lower = query.strip().lower()
    query_words = set(re.findall(r"\w+", query_lower))

    def tier(entry: dict[str, Any]) -> int:
        title = (entry["article"].get("title") or "").strip().lower()
        if title == query_lower:
            return 0
        if query_words and query_words <= set(re.findall(r"\w+", title)):
            return 1
        return 2

    ranked = sorted(by_section_title.values(), key=lambda e: (tier(e), e["index"]))
    trimmed = ranked[:limit]

    articles = []
    for entry in trimmed:
        article = entry["article"]
        section = sections.get(article.get("section_id"))
        articles.append(
            {
                "id": article["id"],
                "title": article.get("title"),
                "snippet": _snippet(article),
                "section": section.get("name") if section else None,
                "url": article.get("html_url"),
            }
        )
    return {"count": len(articles), "articles": articles, "has_more": bool(data.get("next_page"))}


async def get_guide(client: ZendeskClient, article_id: int) -> dict[str, Any]:
    """Get the full content of a single Help Center article, with its HTML body converted to
    readable text (truncated at ~8000 characters, flagged via truncated). If the article is
    restricted, raises a clear error naming the article instead of a generic credentials error."""
    async def fetch_article() -> dict[str, Any]:
        try:
            return await client.get(f"/help_center/articles/{article_id}.json")
        except ZendeskAPIError as exc:
            if exc.status == 403:
                raise ZendeskAPIError(
                    f"Article {article_id} is restricted and the configured credentials do not have "
                    "permission to read it.",
                    status=403,
                ) from exc
            raise

    data, (sections, categories, _truncated_taxonomy) = await asyncio.gather(fetch_article(), _taxonomy(client))
    article = data["article"]
    section = sections.get(article.get("section_id"))
    category = categories.get(section.get("category_id")) if section else None
    body, body_truncated = truncate(_html_to_text(article.get("body") or ""), MAX_BODY_CHARS)

    out = project(article, ARTICLE_DETAIL_FIELDS)
    out["url"] = out.pop("html_url", article.get("html_url"))
    out.pop("section_id", None)
    out["section"] = section.get("name") if section else None
    out["category"] = category.get("name") if category else None
    out["body"] = body
    if body_truncated:
        out["truncated"] = True
    return out


async def list_guide_categories(client: ZendeskClient) -> dict[str, Any]:
    """List Help Center categories with their sections nested inside, for exploratory
    navigation."""
    sections, categories, truncated = await _taxonomy(client)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for section in sections.values():
        grouped.setdefault(section.get("category_id"), []).append(
            {"id": section["id"], "name": section.get("name")}
        )
    result = [
        {"id": category["id"], "name": category.get("name"), "sections": grouped.get(category["id"], [])}
        for category in categories.values()
    ]
    return {"count": len(result), "categories": result, "has_more": truncated}


async def create_guide(
    client: ZendeskClient,
    section: str,
    title: str,
    body: str,
    permission_group: str,
    visibility: str,
    draft: bool,
    locale: str = "pt-br",
) -> dict[str, Any]:
    """Create a Help Center article. Specify draft explicitly: True creates an unpublished
    draft, False publishes it immediately to the Help Center — decide based on what the user
    asked, never default to one or the other. visibility is "everyone" for a publicly visible
    article, or a user segment name/ID to restrict it. section and permission_group accept a
    name or a numeric ID; call list_guide_permissions to discover valid values. body should be
    HTML; plain text is wrapped in paragraphs automatically."""
    sections, _categories, _truncated = await _taxonomy(client)
    section_id = _resolve_ref(list(sections.values()), section, "section")

    if permission_group.isdigit():
        permission_group_id = int(permission_group)
    else:
        permission_data = await client.get("/guide/permission_groups.json")
        permission_group_id = _resolve_ref(
            permission_data.get("permission_groups", []), permission_group, "permission group"
        )

    if visibility.strip().lower() == "everyone":
        user_segment_id = None
    elif visibility.isdigit():
        user_segment_id = int(visibility)
    else:
        segment_data = await client.get("/help_center/user_segments.json")
        user_segment_id = _resolve_ref(segment_data.get("user_segments", []), visibility, "user segment")

    article: dict[str, Any] = {
        "title": title,
        "body": _text_to_html(body),
        "locale": locale,
        "permission_group_id": permission_group_id,
        "user_segment_id": user_segment_id,
        "draft": draft,
    }
    data = await client.post(f"/help_center/sections/{section_id}/articles.json", json={"article": article})
    created = data["article"]
    out = project(created, ARTICLE_WRITE_FIELDS)
    out["url"] = out.pop("html_url", created.get("html_url"))
    out.pop("section_id", None)
    section_obj = sections.get(created.get("section_id"))
    out["section"] = section_obj.get("name") if section_obj else None
    return out


async def update_guide(
    client: ZendeskClient,
    article_id: int,
    title: str | None = None,
    body: str | None = None,
    draft: bool | None = None,
    locale: str = "pt-br",
) -> dict[str, Any]:
    """Update an existing Help Center article's title, body, or draft status. Editing content
    goes through the article's translation for locale (Zendesk does not update title/body via
    the article endpoint directly). Provide at least one of title, body, or draft.

    draft defaults to None (leave publication state unchanged), unlike create_guide's required
    draft: there's no prior state to decide on when creating, but omitting it here is a safe
    no-op that never accidentally publishes or unpublishes an existing article.
    """
    if title is None and body is None and draft is None:
        raise ValueError("Provide at least one of title, body, or draft to update.")
    translation: dict[str, Any] = {}
    if title is not None:
        translation["title"] = title
    if body is not None:
        translation["body"] = _text_to_html(body)
    if draft is not None:
        translation["draft"] = draft
    try:
        data = await client.put(
            f"/help_center/articles/{article_id}/translations/{locale}.json",
            json={"translation": translation},
        )
    except ZendeskAPIError as exc:
        if exc.status == 403:
            raise ZendeskAPIError(
                f"Article {article_id} is restricted, or the configured credentials do not have "
                "permission to publish in its permission group.",
                status=403,
            ) from exc
        raise
    return project(data["translation"], TRANSLATION_FIELDS)


async def list_guide_permissions(client: ZendeskClient) -> dict[str, Any]:
    """List permission groups and user segments, for filling create_guide's permission_group
    and visibility parameters. "everyone" is also accepted as visibility without needing a
    segment from this list."""
    async def fetch_permissions() -> dict[str, Any]:
        try:
            return await client.get("/guide/permission_groups.json")
        except ZendeskAPIError as exc:
            if exc.status == 403:
                raise ZendeskAPIError(
                    "Listing permission groups requires a Help Center manager role, which the "
                    "configured credentials do not have. Pass permission_group as a numeric ID to "
                    "create_guide directly instead.",
                    status=403,
                ) from exc
            raise

    segment_data, permission_data = await asyncio.gather(
        client.get("/help_center/user_segments.json"), fetch_permissions()
    )
    return {
        "permission_groups": project_list(permission_data.get("permission_groups", []), GUIDE_REF_FIELDS),
        "user_segments": project_list(segment_data.get("user_segments", []), GUIDE_REF_FIELDS),
    }

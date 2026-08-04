import pytest

from mcp_zendesk.client import ZendeskAPIError
from mcp_zendesk.tools import fields, groups, guides, search, tickets, users


class FakeZendeskClient:
    def __init__(self, responses: dict[str, dict | list[dict]]):
        self._responses = responses
        self._call_counts: dict[str, int] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def _response_for(self, path: str) -> dict:
        value = self._responses[path]
        if isinstance(value, list):
            index = self._call_counts.get(path, 0)
            self._call_counts[path] = index + 1
            return value[index]
        return value

    async def get(self, path: str, **kwargs) -> dict:
        self.calls.append(("GET", path, kwargs))
        return self._response_for(path)

    async def post(self, path: str, **kwargs) -> dict:
        self.calls.append(("POST", path, kwargs))
        return self._response_for(path)

    async def put(self, path: str, **kwargs) -> dict:
        self.calls.append(("PUT", path, kwargs))
        return self._response_for(path)


@pytest.mark.asyncio
async def test_list_tickets_without_filters_uses_plain_listing():
    client = FakeZendeskClient({"/tickets.json": {"tickets": [{"id": 1}]}})
    result = await tickets.list_tickets(client)
    # Cursor listing has no total; only search reports one.
    assert result == {"count": 1, "total_matches": None, "tickets": [{"id": 1}], "next_cursor": None}
    assert client.calls[0][:2] == ("GET", "/tickets.json")


@pytest.mark.asyncio
async def test_list_tickets_strips_noise_fields():
    raw_ticket = {"id": 1, "subject": "Help", "url": "https://x.zendesk.com/tickets/1.json", "via": {"channel": "web"}}
    client = FakeZendeskClient({"/tickets.json": {"tickets": [raw_ticket]}})
    result = await tickets.list_tickets(client)
    assert result["tickets"] == [{"id": 1, "subject": "Help"}]


@pytest.mark.asyncio
async def test_list_tickets_with_filters_uses_search():
    client = FakeZendeskClient({"/search.json": {"results": [{"id": 2}], "count": 1}})
    result = await tickets.list_tickets(client, status="open", requester_email="a@b.com")
    assert result == {"count": 1, "total_matches": 1, "tickets": [{"id": 2}], "next_cursor": None}
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "/search.json")
    assert "status:open" in kwargs["params"]["query"]
    assert "requester:a@b.com" in kwargs["params"]["query"]


@pytest.mark.asyncio
async def test_list_tickets_reports_zendesk_total_not_just_page_size():
    client = FakeZendeskClient({"/search.json": {"results": [{"id": 1}, {"id": 2}], "count": 317}})
    result = await tickets.list_tickets(client, status="open", limit=2)
    assert result["count"] == 2
    assert result["total_matches"] == 317


@pytest.mark.asyncio
async def test_list_tickets_filtered_branch_uses_offset_pagination():
    client = FakeZendeskClient({"/search.json": {"results": [], "next_page": "https://x/search.json?page=2"}})
    result = await tickets.list_tickets(client, status="open")
    _, _, kwargs = client.calls[0]
    assert "page[size]" not in kwargs["params"]
    assert "page[after]" not in kwargs["params"]
    assert kwargs["params"]["per_page"] == 25
    assert result["next_cursor"] == "2"


@pytest.mark.asyncio
async def test_list_tickets_attaches_sideloaded_names():
    client = FakeZendeskClient(
        {
            "/tickets.json": {
                "tickets": [{"id": 1, "requester_id": 9, "group_id": 5}],
                "users": [{"id": 9, "name": "Alice"}],
                "groups": [{"id": 5, "name": "N1"}],
            }
        }
    )
    result = await tickets.list_tickets(client)
    assert result["tickets"] == [{"id": 1, "requester_id": 9, "group_id": 5, "requester_name": "Alice", "group_name": "N1"}]
    assert client.calls[0][2]["params"]["include"] == "users,groups,organizations"


@pytest.mark.asyncio
async def test_list_tickets_sort_and_cursor_params():
    client = FakeZendeskClient({"/search.json": {"results": [], "next_page": "https://x/search.json?page=6"}})
    result = await tickets.list_tickets(client, status="open", sort_by="priority", sort_order="desc", cursor="5")
    assert result["next_cursor"] == "6"
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["sort_by"] == "priority"
    assert kwargs["params"]["sort_order"] == "desc"
    assert kwargs["params"]["page"] == 5


@pytest.mark.asyncio
async def test_get_ticket_unwraps_ticket_key():
    client = FakeZendeskClient({"/tickets/5.json": {"ticket": {"id": 5}}})
    result = await tickets.get_ticket(client, 5)
    assert result == {"id": 5}


@pytest.mark.asyncio
async def test_get_ticket_strips_noise_fields():
    raw_ticket = {"id": 5, "description": "It broke", "url": "https://x.zendesk.com/tickets/5.json"}
    client = FakeZendeskClient({"/tickets/5.json": {"ticket": raw_ticket}})
    result = await tickets.get_ticket(client, 5)
    assert result == {"id": 5, "description": "It broke"}


@pytest.mark.asyncio
async def test_get_ticket_truncates_long_description():
    long_description = "word " * (tickets.DESCRIPTION_MAX_CHARS)
    client = FakeZendeskClient({"/tickets/5.json": {"ticket": {"id": 5, "description": long_description}}})
    result = await tickets.get_ticket(client, 5)
    assert len(result["description"]) <= tickets.DESCRIPTION_MAX_CHARS + 1
    assert result["description_truncated"] is True


@pytest.mark.asyncio
async def test_get_ticket_drops_unset_custom_fields():
    raw_ticket = {
        "id": 5,
        "custom_fields": [{"id": 1, "value": "x"}, {"id": 2, "value": None}],
    }
    client = FakeZendeskClient(
        {
            "/tickets/5.json": {"ticket": raw_ticket},
            "/ticket_fields.json": {"ticket_fields": [{"id": 1, "title": "Contrato"}]},
        }
    )
    result = await tickets.get_ticket(client, 5)
    assert result["custom_fields"] == [{"id": 1, "name": "Contrato", "value": "x"}]


@pytest.mark.asyncio
async def test_get_ticket_skips_field_lookup_when_no_custom_fields():
    client = FakeZendeskClient({"/tickets/5.json": {"ticket": {"id": 5, "custom_fields": []}}})
    await tickets.get_ticket(client, 5)
    assert [path for _, path, _ in client.calls] == ["/tickets/5.json"]


@pytest.mark.asyncio
async def test_get_ticket_custom_field_without_known_title():
    client = FakeZendeskClient(
        {
            "/tickets/5.json": {"ticket": {"id": 5, "custom_fields": [{"id": 99, "value": "x"}]}},
            "/ticket_fields.json": {"ticket_fields": [{"id": 1, "title": "Contrato"}]},
        }
    )
    result = await tickets.get_ticket(client, 5)
    assert result["custom_fields"] == [{"id": 99, "name": None, "value": "x"}]


@pytest.mark.asyncio
async def test_list_tickets_respects_limit():
    client = FakeZendeskClient({"/tickets.json": {"tickets": []}})
    await tickets.list_tickets(client, limit=10)
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["page[size]"] == 10


@pytest.mark.asyncio
async def test_create_ticket_builds_expected_payload():
    client = FakeZendeskClient({"/tickets.json": {"ticket": {"id": 10}}})
    result = await tickets.create_ticket(
        client,
        subject="Help",
        comment_body="Something broke",
        requester_email="a@b.com",
        priority="high",
        tags=["urgent"],
        custom_fields=[{"id": 123, "value": "x"}],
    )
    assert result == {"id": 10}
    _, path, kwargs = client.calls[0]
    assert path == "/tickets.json"
    ticket = kwargs["json"]["ticket"]
    assert ticket["subject"] == "Help"
    assert ticket["comment"] == {"body": "Something broke"}
    assert ticket["requester"] == {"email": "a@b.com"}
    assert ticket["priority"] == "high"
    assert ticket["tags"] == ["urgent"]
    assert ticket["custom_fields"] == [{"id": 123, "value": "x"}]


@pytest.mark.asyncio
async def test_update_ticket_only_sends_given_fields():
    client = FakeZendeskClient({"/tickets/7.json": {"ticket": {"id": 7, "status": "open"}}})
    result = await tickets.update_ticket(client, 7, status="open")
    assert result == {"id": 7, "status": "open"}
    _, path, kwargs = client.calls[0]
    assert path == "/tickets/7.json"
    assert kwargs["json"]["ticket"] == {"status": "open"}


@pytest.mark.asyncio
async def test_add_comment_public():
    client = FakeZendeskClient({"/tickets/7.json": {"ticket": {"id": 7}}})
    await tickets.add_comment(client, 7, "hello", public=True)
    _, _, kwargs = client.calls[0]
    assert kwargs["json"]["ticket"]["comment"] == {"body": "hello", "public": True}


@pytest.mark.asyncio
async def test_add_comment_internal_note():
    client = FakeZendeskClient({"/tickets/7.json": {"ticket": {"id": 7}}})
    await tickets.add_comment(client, 7, "internal note", public=False)
    _, _, kwargs = client.calls[0]
    assert kwargs["json"]["ticket"]["comment"] == {"body": "internal note", "public": False}


@pytest.mark.asyncio
async def test_add_comment_requires_explicit_public():
    client = FakeZendeskClient({"/tickets/7.json": {"ticket": {"id": 7}}})
    with pytest.raises(TypeError):
        await tickets.add_comment(client, 7, "hello")


@pytest.mark.asyncio
async def test_get_ticket_comments_paginates_and_strips_fields():
    client = FakeZendeskClient(
        {
            "/tickets/7/comments.json": {
                "comments": [
                    {"id": 1, "author_id": 2, "body": "hi", "public": True, "created_at": "t", "html_body": "<p>hi</p>"},
                ],
                "meta": {"has_more": True, "after_cursor": "next"},
            }
        }
    )
    result = await tickets.get_ticket_comments(client, 7)
    assert result == {
        "count": 1,
        "comments": [{"id": 1, "author_id": 2, "body": "hi", "public": True, "created_at": "t"}],
        "next_cursor": "next",
    }


@pytest.mark.asyncio
async def test_get_ticket_comments_uses_cursor():
    client = FakeZendeskClient({"/tickets/7/comments.json": {"comments": []}})
    await tickets.get_ticket_comments(client, 7, cursor="abc")
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["page[after]"] == "abc"


@pytest.mark.asyncio
async def test_get_ticket_comments_respects_limit():
    client = FakeZendeskClient({"/tickets/7/comments.json": {"comments": []}})
    await tickets.get_ticket_comments(client, 7, limit=10)
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["page[size]"] == 10


@pytest.mark.asyncio
async def test_get_ticket_comments_defaults_to_a_small_page():
    client = FakeZendeskClient({"/tickets/7/comments.json": {"comments": []}})
    await tickets.get_ticket_comments(client, 7)
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["page[size]"] == 20
    assert "sort_order" not in kwargs["params"]


@pytest.mark.asyncio
async def test_get_ticket_comments_newest_first():
    client = FakeZendeskClient({"/tickets/7/comments.json": {"comments": []}})
    await tickets.get_ticket_comments(client, 7, limit=3, sort_order="desc")
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["sort_order"] == "desc"
    assert kwargs["params"]["page[size]"] == 3


@pytest.mark.asyncio
async def test_get_ticket_comments_truncates_long_body():
    long_body = "word " * tickets.COMMENT_BODY_MAX_CHARS
    client = FakeZendeskClient(
        {
            "/tickets/7/comments.json": {
                "comments": [{"id": 1, "author_id": 2, "body": long_body, "public": True, "created_at": "t"}],
            }
        }
    )
    result = await tickets.get_ticket_comments(client, 7)
    comment = result["comments"][0]
    assert len(comment["body"]) <= tickets.COMMENT_BODY_MAX_CHARS + 1
    assert comment["truncated"] is True


@pytest.mark.asyncio
async def test_get_ticket_audits_keeps_only_change_events():
    client = FakeZendeskClient(
        {
            "/tickets/7/audits.json": {
                "audits": [
                    {
                        "id": 1,
                        "author_id": 2,
                        "created_at": "t1",
                        "events": [{"type": "Change", "field_name": "status", "previous_value": "new", "value": "open"}],
                    },
                    {
                        "id": 2,
                        "author_id": 3,
                        "created_at": "t2",
                        "events": [{"type": "Comment", "body": "hi"}],
                    },
                ],
            }
        }
    )
    result = await tickets.get_ticket_audits(client, 7)
    assert result["count"] == 1
    assert result["audits"] == [
        {
            "id": 1,
            "author_id": 2,
            "created_at": "t1",
            "changes": [{"field_name": "status", "previous_value": "new", "value": "open"}],
        }
    ]


@pytest.mark.asyncio
async def test_get_ticket_audits_filters_by_field_name():
    client = FakeZendeskClient(
        {
            "/tickets/7/audits.json": {
                "audits": [
                    {
                        "id": 1,
                        "created_at": "t1",
                        "events": [
                            {"type": "Change", "field_name": "status", "previous_value": "new", "value": "open"},
                            {"type": "Change", "field_name": "priority", "previous_value": None, "value": "high"},
                        ],
                    },
                    {
                        "id": 2,
                        "created_at": "t2",
                        "events": [{"type": "Change", "field_name": "priority", "value": "urgent"}],
                    },
                ],
            }
        }
    )
    result = await tickets.get_ticket_audits(client, 7, field_name="status")
    assert result["count"] == 1
    assert result["audits"][0]["changes"] == [
        {"field_name": "status", "previous_value": "new", "value": "open"}
    ]


@pytest.mark.asyncio
async def test_get_ticket_audits_respects_limit():
    client = FakeZendeskClient({"/tickets/7/audits.json": {"audits": []}})
    await tickets.get_ticket_audits(client, 7, limit=10)
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["page[size]"] == 10


@pytest.mark.asyncio
async def test_get_ticket_audits_truncates_long_values():
    long_value = "word " * tickets.AUDIT_VALUE_MAX_CHARS
    client = FakeZendeskClient(
        {
            "/tickets/7/audits.json": {
                "audits": [
                    {
                        "id": 1,
                        "author_id": 2,
                        "created_at": "t1",
                        "events": [
                            {"type": "Change", "field_name": "description", "previous_value": long_value, "value": "short"}
                        ],
                    },
                ],
            }
        }
    )
    result = await tickets.get_ticket_audits(client, 7)
    change = result["audits"][0]["changes"][0]
    assert len(change["previous_value"]) <= tickets.AUDIT_VALUE_MAX_CHARS + 1
    assert change["value"] == "short"


@pytest.mark.asyncio
async def test_search_tickets_scopes_to_type_ticket():
    client = FakeZendeskClient({"/search.json": {"results": [{"id": 1}], "count": 1}})
    result = await search.search_tickets(client, "billing issue")
    assert result == {"count": 1, "total_matches": 1, "tickets": [{"id": 1}], "next_cursor": None}
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["query"] == "type:ticket billing issue"
    assert kwargs["params"]["include"] == "users,groups,organizations"


@pytest.mark.asyncio
async def test_search_tickets_attaches_sideloaded_names():
    client = FakeZendeskClient(
        {
            "/search.json": {
                "results": [{"id": 1, "assignee_id": 4}],
                "users": [{"id": 4, "name": "Bob"}],
            }
        }
    )
    result = await search.search_tickets(client, "billing")
    assert result["tickets"] == [{"id": 1, "assignee_id": 4, "assignee_name": "Bob"}]


@pytest.mark.asyncio
async def test_search_tickets_sort_and_cursor_params():
    client = FakeZendeskClient({"/search.json": {"results": []}})
    await search.search_tickets(client, "billing", sort_by="created_at", sort_order="asc", cursor="3")
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["sort_by"] == "created_at"
    assert kwargs["params"]["sort_order"] == "asc"
    assert kwargs["params"]["page"] == 3


@pytest.mark.asyncio
async def test_search_tickets_respects_limit():
    client = FakeZendeskClient({"/search.json": {"results": []}})
    await search.search_tickets(client, "billing", limit=10)
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["per_page"] == 10


@pytest.mark.asyncio
async def test_search_tickets_uses_offset_pagination_not_cursor_style():
    client = FakeZendeskClient({"/search.json": {"results": [], "next_page": "https://x/search.json?page=2"}})
    result = await search.search_tickets(client, "billing")
    _, _, kwargs = client.calls[0]
    assert "page[size]" not in kwargs["params"]
    assert "page[after]" not in kwargs["params"]
    assert result["next_cursor"] == "2"


def test_offset_page_params_rejects_invalid_cursor():
    with pytest.raises(ValueError, match="Invalid cursor"):
        fields.offset_page_params("not-a-number", limit=25)


@pytest.mark.asyncio
async def test_search_tickets_respects_explicit_type():
    client = FakeZendeskClient({"/search.json": {"results": []}})
    await search.search_tickets(client, "type:organization acme")
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["query"] == "type:organization acme"


@pytest.mark.asyncio
async def test_get_user_by_id():
    client = FakeZendeskClient({"/users/1.json": {"user": {"id": 1, "name": "Alice"}}})
    result = await users.get_user(client, user_id=1)
    assert result == {"id": 1, "name": "Alice"}


@pytest.mark.asyncio
async def test_get_user_strips_noise_fields():
    raw_user = {"id": 1, "name": "Alice", "url": "https://x.zendesk.com/users/1.json", "photo": {"url": "..."}}
    client = FakeZendeskClient({"/users/1.json": {"user": raw_user}})
    result = await users.get_user(client, user_id=1)
    assert result == {"id": 1, "name": "Alice"}


@pytest.mark.asyncio
async def test_get_user_by_email():
    client = FakeZendeskClient({"/users/search.json": {"users": [{"id": 1, "email": "a@b.com"}]}})
    result = await users.get_user(client, email="a@b.com")
    assert result == {"id": 1, "email": "a@b.com"}
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["query"] == "a@b.com"


@pytest.mark.asyncio
async def test_get_user_no_match_raises():
    client = FakeZendeskClient({"/users/search.json": {"users": []}})
    with pytest.raises(ValueError, match="No Zendesk user found"):
        await users.get_user(client, email="ghost@b.com")


@pytest.mark.asyncio
async def test_get_user_requires_exactly_one_selector():
    client = FakeZendeskClient({})
    with pytest.raises(ValueError, match="exactly one"):
        await users.get_user(client)
    with pytest.raises(ValueError, match="exactly one"):
        await users.get_user(client, user_id=1, email="a@b.com")


@pytest.mark.asyncio
async def test_list_organizations():
    client = FakeZendeskClient(
        {"/organizations.json": {"organizations": [{"id": 1}, {"id": 2}], "next_page": "https://x?page=2"}}
    )
    result = await users.list_organizations(client)
    assert result == {"count": 2, "organizations": [{"id": 1}, {"id": 2}], "next_cursor": "2"}
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["per_page"] == 25


@pytest.mark.asyncio
async def test_list_organizations_by_name_uses_autocomplete():
    client = FakeZendeskClient(
        {"/organizations/autocomplete.json": {"organizations": [{"id": 1, "name": "Acme"}]}}
    )
    result = await users.list_organizations(client, name="Acme")
    assert result == {
        "count": 1,
        "organizations": [{"id": 1, "name": "Acme"}],
        "next_cursor": None,
    }
    assert client.calls == [("GET", "/organizations/autocomplete.json", {"params": {"name": "Acme"}})]


@pytest.mark.asyncio
async def test_list_groups():
    client = FakeZendeskClient({"/groups.json": {"groups": [{"id": 1, "name": "N1"}]}})
    result = await groups.list_groups(client)
    assert result == {"count": 1, "groups": [{"id": 1, "name": "N1"}], "has_more": False}


@pytest.mark.asyncio
async def test_list_groups_paginates_past_the_first_page():
    client = FakeZendeskClient(
        {
            "/groups.json": [
                {"groups": [{"id": 1, "name": "N1"}], "next_page": "https://x/groups.json?page=2"},
                {"groups": [{"id": 2, "name": "N2"}], "next_page": None},
            ]
        }
    )
    # A group past the first page used to be invisible, so list_tickets(group="N2") failed.
    assert await groups.resolve_group_id(client, "N2") == 2


@pytest.mark.asyncio
async def test_resolve_group_id_matches_case_insensitively():
    client = FakeZendeskClient({"/groups.json": {"groups": [{"id": 7, "name": "N1"}]}})
    assert await groups.resolve_group_id(client, "n1") == 7


@pytest.mark.asyncio
async def test_resolve_group_id_no_match_raises():
    client = FakeZendeskClient({"/groups.json": {"groups": [{"id": 7, "name": "N1"}]}})
    with pytest.raises(ValueError, match="No Zendesk group found"):
        await groups.resolve_group_id(client, "N2")


@pytest.mark.asyncio
async def test_list_tickets_with_group_name_resolves_to_id():
    client = FakeZendeskClient(
        {
            "/groups.json": {"groups": [{"id": 7, "name": "N1"}]},
            "/search.json": {"results": [{"id": 3}]},
        }
    )
    result = await tickets.list_tickets(client, group="N1")
    assert result == {"count": 1, "total_matches": None, "tickets": [{"id": 3}], "next_cursor": None}
    _, _, kwargs = client.calls[-1]
    assert "group:7" in kwargs["params"]["query"]


@pytest.mark.asyncio
async def test_list_tickets_with_numeric_group_skips_resolution():
    client = FakeZendeskClient({"/search.json": {"results": []}})
    await tickets.list_tickets(client, group="7")
    assert client.calls == [
        (
            "GET",
            "/search.json",
            {
                "params": {
                    "include": "users,groups,organizations",
                    "per_page": 25,
                    "query": "type:ticket group:7",
                }
            },
        )
    ]


_SECTIONS = {"sections": [{"id": 10, "name": "Boletos", "category_id": 200}]}
_CATEGORIES = {"categories": [{"id": 200, "name": "Faturamento"}]}
# search_guides/get_guide name sections by ID instead of walking the whole taxonomy.
_SECTION_10 = {"section": {"id": 10, "name": "Boletos", "category_id": 200}}


@pytest.mark.asyncio
async def test_search_guides_returns_snippet_not_body():
    article = {
        "id": 1,
        "title": "Como emitir boleto",
        "section_id": 10,
        "html_url": "https://x.zendesk.com/hc/pt-br/articles/1",
        "snippet": "Para <em>emitir</em> um boleto, acesse...",
        "body": "<p>corpo completo aqui...</p>",
        "author_id": 5,
        "vote_sum": 10,
        "created_at": "2020-01-01T00:00:00Z",
        "label_names": ["faq"],
        "edited_at": "2024-01-01T00:00:00Z",
    }
    client = FakeZendeskClient(
        {
            "/help_center/articles/search.json": {"results": [article], "count": 1},
            "/help_center/sections/10.json": _SECTION_10,
        }
    )
    result = await guides.search_guides(client, "boleto")
    assert result["articles"] == [
        {
            "id": 1,
            "title": "Como emitir boleto",
            "snippet": "Para emitir um boleto, acesse...",
            "section": "Boletos",
            "url": "https://x.zendesk.com/hc/pt-br/articles/1",
        }
    ]
    assert result["total_matches"] == 1
    # The whole-Help-Center taxonomy walk is what used to cost up to 40 requests per search.
    assert [path for _, path, _ in client.calls] == [
        "/help_center/articles/search.json",
        "/help_center/sections/10.json",
    ]


@pytest.mark.asyncio
async def test_search_guides_dedupes_translations():
    en = {"id": 1, "title": "How to pay", "locale": "en-us", "section_id": 10, "html_url": "u-en",
          "edited_at": "2024-01-01T00:00:00Z"}
    pt = {"id": 1, "title": "Como pagar", "locale": "pt-br", "section_id": 10, "html_url": "u-pt",
          "edited_at": "2024-02-01T00:00:00Z"}
    client = FakeZendeskClient(
        {
            "/help_center/articles/search.json": {"results": [en, pt]},
            "/help_center/sections/10.json": _SECTION_10,
        }
    )
    result = await guides.search_guides(client, "pay")
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_search_guides_ranks_exact_title_first():
    first = {"id": 1, "title": "Reembolso parcial", "section_id": 10, "html_url": "u1",
              "edited_at": "2024-01-01T00:00:00Z"}
    second = {"id": 2, "title": "boleto", "section_id": 11, "html_url": "u2",
              "edited_at": "2024-01-01T00:00:00Z"}
    client = FakeZendeskClient(
        {
            "/help_center/articles/search.json": {"results": [first, second]},
            "/help_center/sections/10.json": _SECTION_10,
            "/help_center/sections/11.json": {"section": {"id": 11, "name": "Cartões", "category_id": 200}},
        }
    )
    result = await guides.search_guides(client, "boleto")
    assert result["articles"][0]["id"] == 2


@pytest.mark.asyncio
async def test_get_guide_cleans_html():
    html_body = (
        "<style>.x{color:red}</style>"
        "<h2>Passo 1</h2>"
        "<p>Acesse o portal &amp; clique em <a href='https://x.com'>continuar</a>.</p>"
        "<ul><li>Item um</li><li>Item dois</li></ul>"
        "<script>alert(1)</script>"
    )
    article = {
        "id": 5,
        "title": "Guia de pagamento",
        "section_id": 10,
        "html_url": "u5",
        "locale": "pt-br",
        "updated_at": "2024-01-01T00:00:00Z",
        "body": html_body,
    }
    client = FakeZendeskClient(
        {
            "/help_center/articles/5.json": {
                "article": article,
                # include=sections,categories sideloads both, so no extra request is needed.
                **_SECTIONS,
                **_CATEGORIES,
            },
        }
    )
    result = await guides.get_guide(client, 5)
    assert [path for _, path, _ in client.calls] == ["/help_center/articles/5.json"]
    assert set(result.keys()) == {"id", "title", "body", "url", "category", "section", "updated_at", "locale"}
    assert "alert(1)" not in result["body"]
    assert "color:red" not in result["body"]
    assert "## Passo 1" in result["body"]
    assert "- Item um" in result["body"]
    assert "- Item dois" in result["body"]
    assert "Acesse o portal & clique em continuar." in result["body"]
    assert result["section"] == "Boletos"
    assert result["category"] == "Faturamento"


@pytest.mark.asyncio
async def test_get_guide_truncates_long_body():
    long_body = "<p>" + ("palavra " * 2000) + "</p>"
    article = {
        "id": 6,
        "title": "Artigo longo",
        "section_id": None,
        "html_url": "u6",
        "locale": "pt-br",
        "updated_at": "2024-01-01T00:00:00Z",
        "body": long_body,
    }
    client = FakeZendeskClient({"/help_center/articles/6.json": {"article": article}})
    result = await guides.get_guide(client, 6)
    assert result["truncated"] is True
    assert len(result["body"]) <= guides.MAX_BODY_CHARS + 1


@pytest.mark.asyncio
async def test_get_guide_falls_back_when_sideload_is_absent():
    article = {
        "id": 7,
        "title": "Sem sideload",
        "section_id": 10,
        "html_url": "u7",
        "locale": "pt-br",
        "updated_at": "2024-01-01T00:00:00Z",
        "body": "<p>corpo</p>",
    }
    client = FakeZendeskClient(
        {
            "/help_center/articles/7.json": {"article": article},
            "/help_center/sections/10.json": _SECTION_10,
            "/help_center/categories/200.json": {"category": {"id": 200, "name": "Faturamento"}},
        }
    )
    result = await guides.get_guide(client, 7)
    assert result["section"] == "Boletos"
    assert result["category"] == "Faturamento"


@pytest.mark.asyncio
async def test_get_guide_survives_restricted_section():
    class _RestrictedSectionClient(FakeZendeskClient):
        async def get(self, path, **kwargs):
            if path.startswith("/help_center/sections/"):
                raise ZendeskAPIError("denied", status=403)
            return await super().get(path, **kwargs)

    article = {"id": 8, "title": "T", "section_id": 10, "html_url": "u8", "body": "<p>x</p>"}
    client = _RestrictedSectionClient({"/help_center/articles/8.json": {"article": article}})
    result = await guides.get_guide(client, 8)
    assert result["section"] is None
    assert result["category"] is None


@pytest.mark.asyncio
async def test_get_guide_restricted_article_message():
    class _RestrictedClient(FakeZendeskClient):
        async def get(self, path, **kwargs):
            if path == "/help_center/articles/999.json":
                raise ZendeskAPIError("denied", status=403)
            return await super().get(path, **kwargs)

    client = _RestrictedClient({})
    with pytest.raises(ZendeskAPIError) as exc_info:
        await guides.get_guide(client, 999)
    assert "restricted" in str(exc_info.value)
    assert "999" in str(exc_info.value)


@pytest.mark.asyncio
async def test_list_guide_categories_nests_sections():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": {
                "sections": [
                    {"id": 10, "name": "Boletos", "category_id": 200},
                    {"id": 11, "name": "Cartões", "category_id": 200},
                    {"id": 12, "name": "Contas", "category_id": 201},
                ]
            },
            "/help_center/categories.json": {
                "categories": [
                    {"id": 200, "name": "Faturamento"},
                    {"id": 201, "name": "Cadastro"},
                ]
            },
        }
    )
    result = await guides.list_guide_categories(client)
    assert result == {
        "count": 2,
        "categories": [
            {"id": 200, "name": "Faturamento", "sections": [
                {"id": 10, "name": "Boletos"}, {"id": 11, "name": "Cartões"}]},
            {"id": 201, "name": "Cadastro", "sections": [{"id": 12, "name": "Contas"}]},
        ],
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_taxonomy_paginates_through_all_sections():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": [
                {"sections": [{"id": 1, "name": "Page1", "category_id": 200}], "next_page": "https://x/sections.json?page=2"},
                {"sections": [{"id": 2, "name": "Page2", "category_id": 200}], "next_page": None},
            ],
            "/help_center/categories.json": {"categories": [{"id": 200, "name": "Cat"}]},
        }
    )
    result = await guides.list_guide_categories(client)
    assert result["has_more"] is False
    section_ids = {s["id"] for s in result["categories"][0]["sections"]}
    assert section_ids == {1, 2}


@pytest.mark.asyncio
async def test_paginate_all_stops_at_max_pages():
    client = FakeZendeskClient(
        {
            "/x.json": [
                {"items": [{"id": 1}], "next_page": "https://x/x.json?page=2"},
                {"items": [{"id": 2}], "next_page": "https://x/x.json?page=3"},
                {"items": [{"id": 3}], "next_page": "https://x/x.json?page=4"},
            ],
        }
    )
    items, truncated = await fields.paginate_all(client, "/x.json", "items", max_pages=2)
    assert len(items) == 2
    assert truncated is True


@pytest.mark.asyncio
async def test_create_guide_builds_expected_payload():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": _SECTIONS,
            "/help_center/categories.json": _CATEGORIES,
            "/help_center/sections/10/articles.json": {
                "article": {
                    "id": 99,
                    "title": "Novo artigo",
                    "html_url": "u99",
                    "section_id": 10,
                    "updated_at": "t",
                    "locale": "pt-br",
                    "draft": True,
                    "permission_group_id": 42,
                    "user_segment_id": None,
                }
            },
        }
    )
    result = await guides.create_guide(
        client, "Boletos", "Novo artigo", "<p>Conteudo</p>", permission_group="42", visibility="everyone", draft=True
    )
    assert result == {
        "id": 99,
        "title": "Novo artigo",
        "updated_at": "t",
        "locale": "pt-br",
        "draft": True,
        "permission_group_id": 42,
        "user_segment_id": None,
        "url": "u99",
        "section": "Boletos",
    }
    assert len(client.calls) == 3
    method, path, kwargs = client.calls[-1]
    assert (method, path) == ("POST", "/help_center/sections/10/articles.json")
    assert kwargs["json"]["article"] == {
        "title": "Novo artigo",
        "body": "<p>Conteudo</p>",
        "locale": "pt-br",
        "permission_group_id": 42,
        "user_segment_id": None,
        "draft": True,
    }


@pytest.mark.asyncio
async def test_create_guide_requires_explicit_draft():
    client = FakeZendeskClient({})
    with pytest.raises(TypeError):
        await guides.create_guide(client, "Boletos", "T", "B", permission_group="1", visibility="everyone")


@pytest.mark.asyncio
async def test_create_guide_resolves_section_and_permission_group_by_name():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": _SECTIONS,
            "/help_center/categories.json": _CATEGORIES,
            "/guide/permission_groups.json": {"permission_groups": [{"id": 42, "name": "Agents and Managers"}]},
            "/help_center/sections/10/articles.json": {
                "article": {
                    "id": 100,
                    "title": "T",
                    "html_url": "u100",
                    "section_id": 10,
                    "updated_at": "t",
                    "locale": "pt-br",
                    "draft": True,
                    "permission_group_id": 42,
                    "user_segment_id": None,
                }
            },
        }
    )
    result = await guides.create_guide(
        client, "Boletos", "T", "B", permission_group="Agents and Managers", visibility="everyone", draft=True
    )
    assert result["permission_group_id"] == 42
    assert client.calls[-1][2]["json"]["article"]["permission_group_id"] == 42


@pytest.mark.asyncio
async def test_create_guide_resolves_visibility_segment_by_name():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": _SECTIONS,
            "/help_center/categories.json": _CATEGORIES,
            "/help_center/user_segments.json": {"user_segments": [{"id": 7, "name": "Signed-in users"}]},
            "/help_center/sections/10/articles.json": {
                "article": {
                    "id": 101,
                    "title": "T",
                    "html_url": "u101",
                    "section_id": 10,
                    "updated_at": "t",
                    "locale": "pt-br",
                    "draft": False,
                    "permission_group_id": 42,
                    "user_segment_id": 7,
                }
            },
        }
    )
    result = await guides.create_guide(
        client, "Boletos", "T", "B", permission_group="42", visibility="Signed-in users", draft=False
    )
    assert result["user_segment_id"] == 7
    assert client.calls[-1][2]["json"]["article"]["user_segment_id"] == 7


@pytest.mark.asyncio
async def test_create_guide_ambiguous_section_name_raises():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": {
                "sections": [
                    {"id": 20, "name": "FAQ", "category_id": 200},
                    {"id": 21, "name": "FAQ", "category_id": 201},
                ]
            },
            "/help_center/categories.json": {"categories": []},
        }
    )
    with pytest.raises(ValueError, match="Multiple"):
        await guides.create_guide(client, "FAQ", "T", "B", permission_group="1", visibility="everyone", draft=True)


@pytest.mark.asyncio
async def test_create_guide_wraps_plain_text_body():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": _SECTIONS,
            "/help_center/categories.json": _CATEGORIES,
            "/help_center/sections/10/articles.json": {
                "article": {"id": 102, "title": "T", "html_url": "u", "section_id": 10, "updated_at": "t", "locale": "pt-br",
                            "draft": True, "permission_group_id": 1, "user_segment_id": None}
            },
        }
    )
    await guides.create_guide(
        client, "Boletos", "T", "Linha um.\n\nLinha dois & tal.", permission_group="1", visibility="everyone", draft=True
    )
    sent_body = client.calls[-1][2]["json"]["article"]["body"]
    assert sent_body == "<p>Linha um.</p>\n<p>Linha dois &amp; tal.</p>"


@pytest.mark.asyncio
async def test_create_guide_passes_html_body_through():
    client = FakeZendeskClient(
        {
            "/help_center/sections.json": _SECTIONS,
            "/help_center/categories.json": _CATEGORIES,
            "/help_center/sections/10/articles.json": {
                "article": {"id": 103, "title": "T", "html_url": "u", "section_id": 10, "updated_at": "t", "locale": "pt-br",
                            "draft": True, "permission_group_id": 1, "user_segment_id": None}
            },
        }
    )
    await guides.create_guide(
        client, "Boletos", "T", "<p>Já é HTML</p>", permission_group="1", visibility="everyone", draft=True
    )
    sent_body = client.calls[-1][2]["json"]["article"]["body"]
    assert sent_body == "<p>Já é HTML</p>"


@pytest.mark.asyncio
async def test_update_guide_uses_translations_endpoint():
    client = FakeZendeskClient(
        {
            "/help_center/articles/5/translations/pt-br.json": {
                "translation": {"id": 1, "title": "T atualizado", "locale": "pt-br", "draft": False, "updated_at": "t2"}
            }
        }
    )
    result = await guides.update_guide(client, 5, title="T atualizado")
    assert result == {"id": 1, "title": "T atualizado", "locale": "pt-br", "draft": False, "updated_at": "t2"}
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("PUT", "/help_center/articles/5/translations/pt-br.json")
    assert kwargs["json"]["translation"] == {"title": "T atualizado"}


@pytest.mark.asyncio
async def test_update_guide_only_sends_given_fields():
    client = FakeZendeskClient({"/help_center/articles/5/translations/pt-br.json": {"translation": {"id": 1}}})
    await guides.update_guide(client, 5, draft=True)
    assert client.calls[0][2]["json"]["translation"] == {"draft": True}


@pytest.mark.asyncio
async def test_update_guide_requires_at_least_one_field():
    client = FakeZendeskClient({})
    with pytest.raises(ValueError, match="at least one"):
        await guides.update_guide(client, 5)


@pytest.mark.asyncio
async def test_update_guide_restricted_article_message():
    class _RestrictedUpdateClient(FakeZendeskClient):
        async def put(self, path, **kwargs):
            if path == "/help_center/articles/999/translations/pt-br.json":
                raise ZendeskAPIError("denied", status=403)
            return await super().put(path, **kwargs)

    client = _RestrictedUpdateClient({})
    with pytest.raises(ZendeskAPIError) as exc_info:
        await guides.update_guide(client, 999, title="X")
    assert "999" in str(exc_info.value)
    assert "permission" in str(exc_info.value)


@pytest.mark.asyncio
async def test_list_guide_permissions_trims_fields():
    client = FakeZendeskClient(
        {
            "/help_center/user_segments.json": {
                "user_segments": [{"id": 7, "name": "Signed-in users", "built_in": True, "user_type": "signed_in_users"}]
            },
            "/guide/permission_groups.json": {
                "permission_groups": [{"id": 42, "name": "Agents and Managers", "built_in": True, "edit": [], "publish": []}]
            },
        }
    )
    result = await guides.list_guide_permissions(client)
    assert result == {
        "permission_groups": [{"id": 42, "name": "Agents and Managers"}],
        "user_segments": [{"id": 7, "name": "Signed-in users"}],
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_list_guide_permissions_restricted_message():
    class _RestrictedPermissionsClient(FakeZendeskClient):
        async def get(self, path, **kwargs):
            if path == "/guide/permission_groups.json":
                raise ZendeskAPIError("denied", status=403)
            return await super().get(path, **kwargs)

    client = _RestrictedPermissionsClient({"/help_center/user_segments.json": {"user_segments": []}})
    with pytest.raises(ZendeskAPIError) as exc_info:
        await guides.list_guide_permissions(client)
    assert "numeric ID" in str(exc_info.value)

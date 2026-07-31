import pytest

from mcp_zendesk.tools import groups, search, tickets, users


class FakeZendeskClient:
    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, path: str, **kwargs) -> dict:
        self.calls.append(("GET", path, kwargs))
        return self._responses[path]

    async def post(self, path: str, **kwargs) -> dict:
        self.calls.append(("POST", path, kwargs))
        return self._responses[path]

    async def put(self, path: str, **kwargs) -> dict:
        self.calls.append(("PUT", path, kwargs))
        return self._responses[path]


@pytest.mark.asyncio
async def test_list_tickets_without_filters_uses_plain_listing():
    client = FakeZendeskClient({"/tickets.json": {"tickets": [{"id": 1}]}})
    result = await tickets.list_tickets(client)
    assert result == {"count": 1, "tickets": [{"id": 1}], "next_cursor": None}
    assert client.calls[0][:2] == ("GET", "/tickets.json")


@pytest.mark.asyncio
async def test_list_tickets_strips_noise_fields():
    raw_ticket = {"id": 1, "subject": "Help", "url": "https://x.zendesk.com/tickets/1.json", "via": {"channel": "web"}}
    client = FakeZendeskClient({"/tickets.json": {"tickets": [raw_ticket]}})
    result = await tickets.list_tickets(client)
    assert result["tickets"] == [{"id": 1, "subject": "Help"}]


@pytest.mark.asyncio
async def test_list_tickets_with_filters_uses_search():
    client = FakeZendeskClient({"/search.json": {"results": [{"id": 2}]}})
    result = await tickets.list_tickets(client, status="open", requester_email="a@b.com")
    assert result == {"count": 1, "tickets": [{"id": 2}], "next_cursor": None}
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("GET", "/search.json")
    assert "status:open" in kwargs["params"]["query"]
    assert "requester:a@b.com" in kwargs["params"]["query"]


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
    client = FakeZendeskClient({"/search.json": {"results": [], "meta": {"has_more": True, "after_cursor": "abc"}}})
    result = await tickets.list_tickets(client, status="open", sort_by="priority", sort_order="desc", cursor="xyz")
    assert result["next_cursor"] == "abc"
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["sort_by"] == "priority"
    assert kwargs["params"]["sort_order"] == "desc"
    assert kwargs["params"]["page[after]"] == "xyz"


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
async def test_search_tickets_scopes_to_type_ticket():
    client = FakeZendeskClient({"/search.json": {"results": [{"id": 1}]}})
    result = await search.search_tickets(client, "billing issue")
    assert result == {"count": 1, "tickets": [{"id": 1}], "next_cursor": None}
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
    await search.search_tickets(client, "billing", sort_by="created_at", sort_order="asc", cursor="abc")
    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["sort_by"] == "created_at"
    assert kwargs["params"]["sort_order"] == "asc"
    assert kwargs["params"]["page[after]"] == "abc"


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
    client = FakeZendeskClient({"/organizations.json": {"organizations": [{"id": 1}, {"id": 2}]}})
    result = await users.list_organizations(client)
    assert result == {"count": 2, "organizations": [{"id": 1}, {"id": 2}]}


@pytest.mark.asyncio
async def test_list_groups():
    client = FakeZendeskClient({"/groups.json": {"groups": [{"id": 1, "name": "N1"}]}})
    result = await groups.list_groups(client)
    assert result == {"count": 1, "groups": [{"id": 1, "name": "N1"}]}


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
    assert result == {"count": 1, "tickets": [{"id": 3}], "next_cursor": None}
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
            {"params": {"include": "users,groups,organizations", "query": "type:ticket group:7"}},
        )
    ]

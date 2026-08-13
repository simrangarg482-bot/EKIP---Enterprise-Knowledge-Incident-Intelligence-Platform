"""Tests for `app.ingestion.connectors.azure_devops` -- same `_FakeResponse`
style as `test_jira.py`/`test_teams.py`, extended with a `post()` method
since this connector's WIQL query + batch-fetch calls are POSTs, unlike
every other connector in this codebase. Tests construct `_AzureDevOpsClient`
directly (bypassing `authenticate`, which does a real `GET _apis/projects`
network call) since `fetch_batch`/`normalize` only ever receive that object
back.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.ingestion.connectors import azure_devops as azure_devops_module
from app.ingestion.connectors.azure_devops import AzureDevOpsConnector, _AzureDevOpsClient


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeHttpClient:
    """`responses[url]` is either a fixed JSON payload or a callable taking
    the request's `json` body and returning a JSON payload (for WIQL/batch
    calls whose response depends on the query/ids sent). `get_urls` also
    covers plain `GET`s (the comments endpoint), unlike WIQL/batch which are
    `POST`s.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self.get_urls: list[str] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any], Any]] = []

    async def post(
        self, url: str, params: dict[str, Any] | None = None, json: Any = None
    ) -> _FakeResponse:
        self.requests.append((url, json))
        self.post_calls.append((url, params or {}, json))
        value = self._responses[url]
        payload = value(json) if callable(value) else value
        return _FakeResponse(payload)

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.get_urls.append(url)
        self.get_calls.append((url, params or {}))
        value = self._responses[url]
        payload = value(params or {}) if callable(value) else value
        return _FakeResponse(payload)


def _client(projects: list[str], organization: str = "acme-corp", **responses: Any) -> _AzureDevOpsClient:
    return _AzureDevOpsClient(
        http=_FakeHttpClient(responses), organization=organization, projects=projects
    )


def _work_item(
    work_item_id: int,
    *,
    title: str = "Checkout fails intermittently",
    description: str | None = "<div>Users report random 500s.</div>",
    work_item_type: str = "Bug",
    state: str = "Active",
    assigned_to: str | None = "Jane Doe",
    comment_count: int | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "System.Title": title,
        "System.WorkItemType": work_item_type,
        "System.State": state,
        "System.CreatedDate": "2026-07-01T00:00:00Z",
        "System.ChangedDate": "2026-07-15T00:00:00Z",
    }
    if description is not None:
        fields["System.Description"] = description
    if assigned_to is not None:
        fields["System.AssignedTo"] = {"displayName": assigned_to}
    if comment_count is not None:
        fields["System.CommentCount"] = comment_count
    return {
        "id": work_item_id,
        "url": f"https://dev.azure.com/acme-corp/_apis/wit/workItems/{work_item_id}",
        "fields": fields,
    }


# --- normalize ---------------------------------------------------------------


def test_normalize_full_work_item() -> None:
    connector = AzureDevOpsConnector()
    raw_item = _work_item(42)
    raw_item["_project"] = "ProjA"
    raw_item["_organization"] = "acme-corp"

    doc = connector.normalize(raw_item)

    assert doc.source == "azure_devops"
    assert doc.external_id == "ProjA:42"
    assert doc.title == "Checkout fails intermittently"
    assert doc.content == "Checkout fails intermittently\n\n<div>Users report random 500s.</div>"
    assert doc.source_url == "https://dev.azure.com/acme-corp/ProjA/_workitems/edit/42"
    assert doc.metadata == {
        "project": "ProjA",
        "work_item_type": "Bug",
        "state": "Active",
        "assigned_to": "Jane Doe",
        "created": "2026-07-01T00:00:00Z",
        "updated": "2026-07-15T00:00:00Z",
    }


def test_normalize_work_item_without_description_or_assignee() -> None:
    connector = AzureDevOpsConnector()
    raw_item = _work_item(43, description=None, assigned_to=None)
    raw_item["_project"] = "ProjA"
    raw_item["_organization"] = "acme-corp"

    doc = connector.normalize(raw_item)

    assert doc.content == "Checkout fails intermittently"
    assert "assigned_to" not in doc.metadata


def test_normalize_work_item_appends_comments_after_delimiter() -> None:
    connector = AzureDevOpsConnector()
    raw_item = _work_item(44, comment_count=2)
    raw_item["_project"] = "ProjA"
    raw_item["_organization"] = "acme-corp"
    raw_item["_comments_text"] = "Jane Doe: Investigating.\n\nJohn Roe: Fixed by restart."

    doc = connector.normalize(raw_item)

    assert doc.content == (
        "Checkout fails intermittently\n\n<div>Users report random 500s.</div>"
        "\n\n--- Comments ---\n\nJane Doe: Investigating.\n\nJohn Roe: Fixed by restart."
    )
    assert doc.metadata["comments_count"] == "2"


def test_normalize_work_item_without_comments_omits_delimiter_and_count() -> None:
    connector = AzureDevOpsConnector()
    raw_item = _work_item(45)
    raw_item["_project"] = "ProjA"
    raw_item["_organization"] = "acme-corp"
    raw_item["_comments_text"] = ""

    doc = connector.normalize(raw_item)

    assert "--- Comments ---" not in doc.content
    assert "comments_count" not in doc.metadata


# --- fetch_batch ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_batch_single_batch_exhausts_project() -> None:
    connector = AzureDevOpsConnector()
    wiql_payload = {"workItems": [{"id": 1}, {"id": 2}]}
    batch_payload = {"value": [_work_item(1), _work_item(2)]}
    client = _client(
        ["ProjA"],
        **{
            "ProjA/_apis/wit/wiql": wiql_payload,
            "_apis/wit/workitemsbatch": batch_payload,
        },
    )

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert [item["id"] for item in result.items] == [1, 2]
    assert all(item["_project"] == "ProjA" for item in result.items)
    assert all(item["_organization"] == "acme-corp" for item in result.items)
    assert result.has_more is False
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_fetch_batch_more_ids_than_batch_size_advances_batch_start(monkeypatch) -> None:
    connector = AzureDevOpsConnector()
    ids = [{"id": i} for i in range(1, 251)]  # 250 ids, > _BATCH_SIZE (200)
    wiql_payload = {"workItems": ids}
    batch_payload = {"value": [_work_item(i) for i in range(1, 201)]}
    client = _client(
        ["ProjA"],
        **{
            "ProjA/_apis/wit/wiql": wiql_payload,
            "_apis/wit/workitemsbatch": batch_payload,
        },
    )

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert len(result.items) == 200
    assert result.has_more is True
    next_state = json.loads(result.next_cursor)
    assert next_state == {"project_index": 0, "batch_start": 200}


@pytest.mark.asyncio
async def test_fetch_batch_project_exhausted_advances_to_next_project() -> None:
    connector = AzureDevOpsConnector()
    wiql_payload = {"workItems": [{"id": 1}]}
    batch_payload = {"value": [_work_item(1)]}
    client = _client(
        ["ProjA", "ProjB"],
        **{
            "ProjA/_apis/wit/wiql": wiql_payload,
            "_apis/wit/workitemsbatch": batch_payload,
        },
    )

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert result.has_more is True
    next_state = json.loads(result.next_cursor)
    assert next_state == {"project_index": 1, "batch_start": 0}


@pytest.mark.asyncio
async def test_fetch_batch_no_more_projects_returns_empty() -> None:
    connector = AzureDevOpsConnector()
    client = _AzureDevOpsClient(
        http=_FakeHttpClient({}), organization="acme-corp", projects=[]
    )

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert result.items == []
    assert result.has_more is False
    assert result.next_cursor is None


@pytest.mark.asyncio
async def test_fetch_batch_empty_project_returns_no_items_and_advances() -> None:
    connector = AzureDevOpsConnector()
    wiql_payload = {"workItems": []}
    client = _client(["ProjA", "ProjB"], **{"ProjA/_apis/wit/wiql": wiql_payload})

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert result.items == []
    assert result.has_more is True
    next_state = json.loads(result.next_cursor)
    assert next_state == {"project_index": 1, "batch_start": 0}


@pytest.mark.asyncio
async def test_fetch_batch_includes_since_in_wiql() -> None:
    connector = AzureDevOpsConnector()
    captured: dict[str, Any] = {}

    def fake_wiql(body: Any) -> dict[str, Any]:
        captured.update(body)
        return {"workItems": []}

    client = _client(["ProjA"], **{"ProjA/_apis/wit/wiql": fake_wiql})
    since = datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)

    await connector.fetch_batch(client, since=since, cursor=None)

    assert "[System.ChangedDate] >= '2026-07-01T12:30:00Z'" in captured["query"]
    assert "ORDER BY [System.ChangedDate] ASC" in captured["query"]


@pytest.mark.asyncio
async def test_fetch_batch_resumes_from_cursor() -> None:
    connector = AzureDevOpsConnector()
    wiql_payload = {"workItems": [{"id": i} for i in range(1, 4)]}  # 3 ids
    batch_calls: list[list[int]] = []

    def fake_batch(body: Any) -> dict[str, Any]:
        batch_calls.append(body["ids"])
        return {"value": [_work_item(i) for i in body["ids"]]}

    client = _client(
        ["ProjA"],
        **{"ProjA/_apis/wit/wiql": wiql_payload, "_apis/wit/workitemsbatch": fake_batch},
    )
    cursor = json.dumps({"project_index": 0, "batch_start": 2})

    result = await connector.fetch_batch(client, since=None, cursor=cursor)

    assert batch_calls == [[3]]
    assert [item["id"] for item in result.items] == [3]
    assert result.has_more is False


@pytest.mark.asyncio
async def test_fetch_batch_fetches_comments_when_work_item_has_any() -> None:
    connector = AzureDevOpsConnector()
    wiql_payload = {"workItems": [{"id": 1}]}
    batch_payload = {"value": [_work_item(1, comment_count=2)]}
    comments_payload = {
        "comments": [
            {"createdBy": {"displayName": "Jane Doe"}, "text": "Investigating."},
            {"createdBy": {"displayName": "John Roe"}, "text": "Fixed by restart."},
        ]
    }
    client = _client(
        ["ProjA"],
        **{
            "ProjA/_apis/wit/wiql": wiql_payload,
            "_apis/wit/workitemsbatch": batch_payload,
            "ProjA/_apis/wit/workItems/1/comments": comments_payload,
        },
    )

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert "ProjA/_apis/wit/workItems/1/comments" in client.http.get_urls
    assert result.items[0]["_comments_text"] == (
        "Jane Doe: Investigating.\n\nJohn Roe: Fixed by restart."
    )


@pytest.mark.asyncio
async def test_fetch_batch_skips_comments_call_when_work_item_has_none() -> None:
    connector = AzureDevOpsConnector()
    wiql_payload = {"workItems": [{"id": 1}]}
    batch_payload = {"value": [_work_item(1, comment_count=0)]}
    client = _client(
        ["ProjA"],
        **{"ProjA/_apis/wit/wiql": wiql_payload, "_apis/wit/workitemsbatch": batch_payload},
    )

    result = await connector.fetch_batch(client, since=None, cursor=None)

    assert client.http.get_urls == []
    assert result.items[0]["_comments_text"] == ""


# --- WIQL date precision --------------------------------------------------


def _wiql_params(client) -> dict[str, Any]:
    return next(params for url, params, _body in client.http.post_calls if url.endswith("/wiql"))


@pytest.mark.asyncio
async def test_incremental_query_sends_time_precision() -> None:
    """Regression test for a real, live-reproduced HTTP 400. WIQL defaults to
    date precision and rejects a datetime literal outright
    ("You cannot supply a time with the date when running a query using date
    precision"). `since` is non-None for every sync after the first, so
    without `timePrecision=true` every incremental Azure DevOps sync failed
    while only the initial full sync worked.
    """
    connector = AzureDevOpsConnector()
    client = _client(["ProjA"], **{"ProjA/_apis/wit/wiql": {"workItems": []}})
    since = datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)

    await connector.fetch_batch(client, since=since, cursor=None)

    params = _wiql_params(client)
    assert params["timePrecision"] == "true"
    assert params["api-version"] == "7.1"


@pytest.mark.asyncio
async def test_full_sync_query_omits_time_precision() -> None:
    """A full sync builds no datetime literal, so the flag would be
    meaningless -- and omitting it keeps the first sync's request byte-for-
    byte as it was before the incremental fix.
    """
    connector = AzureDevOpsConnector()
    client = _client(["ProjA"], **{"ProjA/_apis/wit/wiql": {"workItems": []}})

    await connector.fetch_batch(client, since=None, cursor=None)

    assert "timePrecision" not in _wiql_params(client)


# --- API version pinning --------------------------------------------------


def test_comments_endpoint_pinned_to_currently_documented_preview_version() -> None:
    """`wit/comments` is preview-only, and preview revisions can be
    withdrawn without the notice a GA version gets -- so a superseded pin is
    a standing outage risk. `preview.4` is what Microsoft currently
    documents; this previously pinned the superseded `preview.3`.
    """
    assert azure_devops_module._COMMENTS_API_VERSION == "7.1-preview.4"
    # The stable endpoints must NOT have been dragged onto a preview version.
    assert azure_devops_module._API_VERSION == "7.1"


@pytest.mark.asyncio
async def test_comments_call_sends_the_preview_api_version() -> None:
    """Asserts the version actually reaches the wire, not just that the
    constant holds the right string.
    """
    wiql_payload = {"workItems": [{"id": 1}]}
    batch_payload = {"value": [_work_item(1, comment_count=1)]}
    comments_payload = {"comments": [{"createdBy": {"displayName": "Jane"}, "text": "hi"}]}
    client = _client(
        ["ProjA"],
        **{
            "ProjA/_apis/wit/wiql": wiql_payload,
            "_apis/wit/workitemsbatch": batch_payload,
            "ProjA/_apis/wit/workItems/1/comments": comments_payload,
        },
    )

    await AzureDevOpsConnector().fetch_batch(client, since=None, cursor=None)

    comment_calls = [
        params for url, params in client.http.get_calls if url.endswith("/comments")
    ]
    assert comment_calls, "the comments endpoint was never called"
    assert comment_calls[0]["api-version"] == "7.1-preview.4"


def test_decode_cursor_defaults_to_first_project() -> None:
    assert AzureDevOpsConnector._decode_cursor(None) == (0, 0)


def test_decode_cursor_parses_envelope() -> None:
    cursor = json.dumps({"project_index": 1, "batch_start": 200})
    assert AzureDevOpsConnector._decode_cursor(cursor) == (1, 200)

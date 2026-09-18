"""Comment pagination, ADF text, field decoupling, and Jira 429 retries."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from jira.exceptions import JIRAError
from jira.resources import Issue

from onyx.connectors.cross_connector_utils.rate_limit_wrapper import (
    RateLimitTriedTooManyTimesError,
)
from onyx.connectors.jira.connector import process_jira_issue
from onyx.connectors.jira.utils import (
    extract_text_from_adf,
    fetch_all_jira_comments,
    get_comment_strs,
    jira_sdk_call,
    jira_session_request,
)


def _issue_from_raw(raw: dict[str, Any]) -> MagicMock:
    issue = MagicMock(spec=Issue)
    issue.key = raw["key"]
    issue.raw = raw
    issue.fields = SimpleNamespace()
    return issue


def test_adf_keeps_mentions_status_emoji_and_links() -> None:
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Ping"},
                    {
                        "type": "mention",
                        "attrs": {"text": "@Jalla venkata shivananda", "id": "acct-j"},
                    },
                    {"type": "text", "text": "status"},
                    {
                        "type": "status",
                        "attrs": {"text": "In Progress", "color": "blue"},
                    },
                    {"type": "emoji", "attrs": {"shortName": ":check:", "text": "✅"}},
                    {
                        "type": "text",
                        "text": "docs",
                        "marks": [
                            {"type": "link", "attrs": {"href": "https://example.com"}}
                        ],
                    },
                ],
            }
        ],
    }
    text = extract_text_from_adf(adf)
    assert "@Jalla venkata shivananda" in text
    assert "[In Progress]" in text
    assert "✅" in text
    assert "[docs](https://example.com)" in text


def test_real_rd_ticket_adf_keeps_links_media_and_smart_links() -> None:
    """Live RD-63 / RD-67 ADF nodes that the old walker omitted."""
    rd63 = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Test data: in release "},
                    {
                        "type": "text",
                        "text": "REL-0005",
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {
                                    "href": "https://desk-lime-pi.vercel.app/releases/cmr34d8rl01dlx8dkfxtn8jf8"
                                },
                            }
                        ],
                    },
                    {"type": "text", "text": ", approval : "},
                    {
                        "type": "text",
                        "text": "APR-0036",
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {
                                    "href": "https://desk-lime-pi.vercel.app/approvals/cmsvsiy7tlszev4yb"
                                },
                            }
                        ],
                    },
                ],
            }
        ],
    }
    rd67 = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "inlineCard",
                        "attrs": {
                            "url": "https://releasedesk-team.atlassian.net/browse/RD-134"
                        },
                    }
                ],
            },
            {
                "type": "mediaSingle",
                "content": [
                    {
                        "type": "media",
                        "attrs": {
                            "alt": "image-20260825-053130.png",
                            "id": "8a325ac9-5df6-44f6-8b27-1b8c93a7b8d0",
                        },
                    }
                ],
            },
        ],
    }
    rd63_text = extract_text_from_adf(rd63)
    rd67_text = extract_text_from_adf(rd67)
    assert (
        "[REL-0005](https://desk-lime-pi.vercel.app/releases/cmr34d8rl01dlx8dkfxtn8jf8)"
        in rd63_text
    )
    assert (
        "[APR-0036](https://desk-lime-pi.vercel.app/approvals/cmsvsiy7tlszev4yb)"
        in rd63_text
    )
    assert "https://releasedesk-team.atlassian.net/browse/RD-134" in rd67_text
    assert "[media: image-20260825-053130.png]" in rd67_text


def test_adf_unknown_node_has_fallback_and_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "futureWidget",
                "attrs": {"text": "visible-fallback"},
                "content": [{"type": "text", "text": "child"}],
            }
        ],
    }
    text = extract_text_from_adf(adf)
    assert "visible-fallback" in text
    assert "child" in text
    assert "futureWidget" in caplog.text


def test_comment_pagination_fetches_every_page() -> None:
    client = MagicMock()
    client._get_url.return_value = (
        "https://example.atlassian.net/rest/api/3/issue/RD-1/comment"
    )
    pages = [
        {
            "startAt": 0,
            "maxResults": 2,
            "total": 5,
            "comments": [{"body": "c1"}, {"body": "c2"}],
        },
        {
            "startAt": 2,
            "maxResults": 2,
            "total": 5,
            "comments": [{"body": "c3"}, {"body": "c4"}],
        },
        {"startAt": 4, "maxResults": 2, "total": 5, "comments": [{"body": "c5"}]},
    ]
    responses = []
    for page in pages:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.json.return_value = page
        responses.append(resp)
    client._session.get.side_effect = responses

    comments = fetch_all_jira_comments(client, "RD-1")
    assert [comment["body"] for comment in comments] == ["c1", "c2", "c3", "c4", "c5"]
    assert client._session.get.call_count == 3


def test_comment_pagination_follows_next_page_token() -> None:
    client = MagicMock()
    client._get_url.return_value = (
        "https://example.atlassian.net/rest/api/3/issue/RD-2/comment"
    )
    first = MagicMock()
    first.status_code = 200
    first.headers = {}
    first.json.return_value = {
        "comments": [{"body": "page-1"}],
        "nextPageToken": "tok-2",
    }
    second = MagicMock()
    second.status_code = 200
    second.headers = {}
    second.json.return_value = {"comments": [{"body": "page-2"}]}
    client._session.get.side_effect = [first, second]

    comments = fetch_all_jira_comments(client, "RD-2")
    assert [comment["body"] for comment in comments] == ["page-1", "page-2"]
    assert client._session.get.call_args_list[1].kwargs["params"]["nextPageToken"] == (
        "tok-2"
    )


def test_get_comment_strs_prefixes_display_name_not_email() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-24",
            "fields": {
                "comment": {
                    "total": 2,
                    "comments": [
                        {
                            "body": "ship it",
                            "author": {
                                "displayName": "Ada",
                                "emailAddress": "ada@example.com",
                            },
                        },
                        {"body": "no name"},
                    ],
                }
            },
        }
    )
    lines = get_comment_strs(issue)
    assert lines == ["Ada: ship it", "Unknown: no name"]
    assert all("ada@example.com" not in line for line in lines)


def test_get_comment_strs_pages_when_first_page_is_truncated() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-23",
            "fields": {
                "comment": {
                    "total": 3,
                    "comments": [{"body": "first-page-only"}],
                }
            },
        }
    )
    client = MagicMock()
    client._get_url.return_value = (
        "https://example.atlassian.net/rest/api/3/issue/RD-23/comment"
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {
        "startAt": 0,
        "total": 3,
        "comments": [{"body": "c1"}, {"body": "c2"}, {"body": "c3"}],
    }
    client._session.get.return_value = resp

    assert get_comment_strs(issue, jira_client=client) == [
        "Unknown: c1",
        "Unknown: c2",
        "Unknown: c3",
    ]


def test_truncated_comments_without_client_raise() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-20",
            "fields": {
                "summary": "s",
                "description": "d",
                "comment": {
                    "total": 21,
                    "comments": [{"body": f"c{i}"} for i in range(20)],
                },
                "updated": "2023-01-01T00:00:00+0000",
                "created": "2023-01-01T00:00:00+0000",
            },
        }
    )
    with pytest.raises(RuntimeError, match="only 20 of 21 comments"):
        get_comment_strs(issue)


def test_description_getattr_failure_does_not_drop_title() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-21",
            "fields": {
                "summary": "Keep this title",
                "description": "Keep this description",
                "comment": {"comments": [{"body": "Keep this comment"}]},
                "updated": "2023-01-01T00:00:00+0000",
                "created": "2023-01-01T00:00:00+0000",
            },
        }
    )

    class _Boom:
        @property
        def description(self) -> str:
            raise RuntimeError("lazy description")

        @property
        def comment(self) -> str:
            raise RuntimeError("lazy comments")

        @property
        def summary(self) -> str:
            raise RuntimeError("lazy summary")

    issue.fields = _Boom()
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert "Keep this title" in doc.title
    assert "Keep this description" in doc.sections[0].text
    assert "Keep this comment" in doc.sections[0].text
    assert "Unknown: Keep this comment" in doc.sections[0].text


def test_comment_read_failure_keeps_title_and_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-22",
            "fields": {
                "summary": "Title survives",
                "description": "Description survives",
                "updated": "2023-01-01T00:00:00+0000",
                "created": "2023-01-01T00:00:00+0000",
            },
        }
    )

    def _boom(*_args: Any, **_kwargs: Any) -> list[str]:
        raise ValueError("comments exploded")

    monkeypatch.setattr("onyx.connectors.jira.connector.get_comment_strs", _boom)
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert "Title survives" in doc.title
    assert "Description survives" in doc.sections[0].text


def test_jira_session_retries_429(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(
        "onyx.connectors.cross_connector_utils.rate_limit_wrapper.time.sleep",
        slept.append,
    )
    session = MagicMock()
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "2"}
    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {}
    session.get.side_effect = [limited, ok]

    response = jira_session_request(session, "get", "https://example/jira")
    assert response.status_code == 200
    assert slept == [2]


def test_jira_sdk_retries_429(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("onyx.connectors.jira.utils.time.sleep", slept.append)
    calls = {"n": 0}

    def _search() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            response = MagicMock()
            response.headers = {"Retry-After": "1"}
            raise JIRAError(status_code=429, response=response)
        return "ok"

    assert jira_sdk_call(_search) == "ok"
    assert slept == [1]


def test_jira_sdk_gives_up_after_429_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onyx.connectors.jira.utils.time.sleep", lambda _: None)

    def _always_limited() -> None:
        raise JIRAError(status_code=429)

    with pytest.raises(RateLimitTriedTooManyTimesError):
        jira_sdk_call(_always_limited)

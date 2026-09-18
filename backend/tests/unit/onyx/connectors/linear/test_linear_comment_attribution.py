"""Linear folded comments prefix each speaker."""

import inspect

from onyx.connectors.linear.connector import (
    LinearConnector,
    _linear_comment_section_text,
)


def test_linear_comment_prefixes_speaker() -> None:
    text = _linear_comment_section_text(
        {"body": "ship it", "user": {"name": "Ada", "email": "ada@example.com"}}
    )
    assert text == "Ada: ship it"
    assert "ada@example.com" not in text


def test_linear_comment_unknown_when_user_missing() -> None:
    assert _linear_comment_section_text({"body": "no name"}) == "Unknown: no name"
    assert _linear_comment_section_text({"body": "blank", "user": {}}) == "Unknown: blank"
    assert _linear_comment_section_text({}) == "Unknown: "


def test_linear_issue_query_fetches_comment_user_name() -> None:
    source = inspect.getsource(LinearConnector._process_issues)
    assert "comments" in source
    assert "user {" in source
    assert "email" not in source.split("comments")[1].split("pageInfo")[0]



def test_linear_comment_prefixes_speaker() -> None:
    text = _linear_comment_section_text(
        {"body": "ship it", "user": {"name": "Ada", "email": "ada@example.com"}}
    )
    assert text == "Ada: ship it"
    assert "ada@example.com" not in text


def test_linear_comment_unknown_when_user_missing() -> None:
    assert _linear_comment_section_text({"body": "no name"}) == "Unknown: no name"
    assert _linear_comment_section_text({"body": "blank", "user": {}}) == "Unknown: blank"
    assert _linear_comment_section_text({}) == "Unknown: "

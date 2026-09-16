"""Author lookup is optional. Missing users:read must not fail a Slack thread."""

from typing import Any
from unittest.mock import MagicMock, patch

from onyx.connectors.models import BasicExpertInfo
from onyx.connectors.slack.source_operations import SlackApiError
from onyx.connectors.slack.utils import expert_info_from_slack_id, slack_document_metadata


def test_missing_users_read_returns_none_instead_of_raising() -> None:
    cache: dict[str, Any] = {}
    fetch = MagicMock(
        side_effect=SlackApiError(
            "The request to the Slack API failed.",
            {"ok": False, "error": "missing_scope"},
        )
    )
    result = expert_info_from_slack_id("U1", fetch, cache)
    assert result is None
    assert cache["U1"] is None
    fetch.assert_called_once_with("U1")


def test_missing_users_read_logs_error_slug_not_body() -> None:
    cache: dict[str, Any] = {}
    fetch = MagicMock(
        side_effect=SlackApiError(
            "The request to the Slack API failed.",
            {"ok": False, "error": "missing_scope", "needed": "users:read"},
        )
    )
    with patch("onyx.connectors.slack.utils.logger") as mock_logger:
        expert_info_from_slack_id("U1", fetch, cache)
    mock_logger.warning.assert_called_once()
    args = mock_logger.warning.call_args[0]
    assert args[1] == "U1"
    assert args[2] == "missing_scope"
    logged = " ".join(str(part) for part in args)
    assert "users:read" not in logged
    assert "xoxb-" not in logged


def test_ok_false_logs_error_slug() -> None:
    cache: dict[str, Any] = {}
    response = MagicMock()
    response.ok = False
    response.error = "invalid_auth"
    response.user = {}
    fetch = MagicMock(return_value=response)
    with patch("onyx.connectors.slack.utils.logger") as mock_logger:
        result = expert_info_from_slack_id("U9", fetch, cache)
    assert result is None
    assert mock_logger.warning.call_args[0][2] == "invalid_auth"


def test_wrapped_user_lookup_error_does_not_fail_the_thread() -> None:
    cache: dict[str, Any] = {}
    fetch = MagicMock(side_effect=RuntimeError("users.info wrapper"))
    result = expert_info_from_slack_id("U2", fetch, cache)
    assert result is None
    assert cache["U2"] is None


def test_slack_document_metadata_omits_email_and_unknown() -> None:
    assert slack_document_metadata("social", None) == {"channel": "social"}
    email_only = BasicExpertInfo(email="hidden@example.com")
    assert slack_document_metadata("social", email_only) == {"channel": "social"}
    named = BasicExpertInfo(display_name="Ada Lovelace", email="hidden@example.com")
    assert slack_document_metadata("social", named) == {
        "channel": "social",
        "author": "Ada Lovelace",
    }
    unknown = BasicExpertInfo(display_name="Unknown")
    assert slack_document_metadata("social", unknown) == {"channel": "social"}

"""Author lookup is optional. Missing users:read must not fail a Slack thread."""

from typing import Any
from unittest.mock import MagicMock

from onyx.connectors.slack.source_operations import SlackApiError
from onyx.connectors.slack.utils import expert_info_from_slack_id


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


def test_wrapped_user_lookup_error_does_not_fail_the_thread() -> None:
    cache: dict[str, Any] = {}
    fetch = MagicMock(side_effect=RuntimeError("users.info wrapper"))
    result = expert_info_from_slack_id("U2", fetch, cache)
    assert result is None
    assert cache["U2"] is None

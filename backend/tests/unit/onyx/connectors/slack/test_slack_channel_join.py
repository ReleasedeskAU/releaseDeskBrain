"""Join failures must skip the channel, not abort Slack indexing.

A bot cannot join private channels, and public join fails without
``channels:join``. Those used to raise and stall the checkpoint on the same
channel with 0 documents.
"""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from onyx.connectors.slack.connector import _get_messages, get_channel_messages
from onyx.connectors.slack.models import ChannelType
from onyx.connectors.slack.source_operations import SlackApiError, SlackHistoryPage


def _channel(**overrides: Any) -> ChannelType:
    base: dict[str, Any] = {
        "id": "C1",
        "name": "general",
        "is_member": False,
        "is_private": False,
    }
    base.update(overrides)
    return cast(ChannelType, base)


def _join_error(slug: str) -> SlackApiError:
    return SlackApiError("The request to the Slack API failed.", {"ok": False, "error": slug})


def test_private_channel_is_skipped_without_join() -> None:
    client = MagicMock()
    messages, has_more = _get_messages(
        _channel(id="G1", name="secret", is_private=True), client
    )
    assert messages == []
    assert has_more is False
    client.join_channel.assert_not_called()
    client.fetch_channel_history.assert_not_called()


def test_missing_join_scope_skips_public_channel() -> None:
    client = MagicMock()
    client.join_channel.side_effect = _join_error("missing_scope")
    messages, has_more = _get_messages(_channel(), client)
    assert messages == []
    assert has_more is False
    client.fetch_channel_history.assert_not_called()


def test_archived_join_skips_channel() -> None:
    client = MagicMock()
    client.join_channel.side_effect = _join_error("is_archived")
    messages, has_more = _get_messages(_channel(), client)
    assert messages == []
    assert has_more is False


def test_invalid_auth_on_join_still_fails() -> None:
    client = MagicMock()
    client.join_channel.side_effect = _join_error("invalid_auth")
    with pytest.raises(SlackApiError):
        _get_messages(_channel(), client)


def test_history_not_in_channel_skips_without_abort() -> None:
    client = MagicMock()
    client.fetch_channel_history.side_effect = _join_error("not_in_channel")
    messages, has_more = _get_messages(_channel(is_member=True), client)
    assert messages == []
    assert has_more is False


def test_history_missing_scope_still_fails() -> None:
    client = MagicMock()
    client.fetch_channel_history.side_effect = _join_error("missing_scope")
    with pytest.raises(SlackApiError):
        _get_messages(_channel(is_member=True), client)


def test_member_channel_does_not_join() -> None:
    page = SlackHistoryPage(ok=True, messages=[{"ts": "1.0", "text": "hi"}])
    client = MagicMock()
    client.fetch_channel_history.return_value = iter([page])
    messages, has_more = _get_messages(_channel(is_member=True), client)
    assert len(messages) == 1
    assert has_more is False
    client.join_channel.assert_not_called()


def test_slim_path_skips_unjoinable_public_channel() -> None:
    client = MagicMock()
    client.join_channel.side_effect = _join_error("method_not_supported_for_channel_type")
    batches = list(get_channel_messages(client, _channel()))
    assert batches == []
    client.fetch_channel_history.assert_not_called()

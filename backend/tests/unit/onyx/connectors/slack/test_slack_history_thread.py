"""History rows without thread_ts must still fold into the parent thread.

C07-05 extras were indexed as channel__reply_ts with permalinks that omit
?thread_ts=. conversations.replies accepts a reply ts and returns the parent
first. Do not skip thread_broadcast globally — documented broadcasts include
thread_ts and already fold to the parent id.
"""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from onyx.connectors.slack.connector import (
    _doc_id_ts_for_history_message,
    _history_thread,
    _message_thread_ts,
    _message_to_doc,
    _thread_root_ts,
)
from onyx.connectors.slack.models import ChannelType, MessageType

PARENT = "1789562820.740899"
REPLY_A = "1789562850.384339"
REPLY_B = "1789562853.651989"
CHANNEL_ID = "C0C1GG11S5D"


def _channel() -> ChannelType:
    return cast(ChannelType, {"id": CHANNEL_ID, "name": "social"})


def _msg(ts: str, text: str = "hello", **extra: Any) -> MessageType:
    data: dict[str, Any] = {"type": "message", "user": "U1", "text": text, "ts": ts}
    data.update(extra)
    return cast(MessageType, data)


def _slack_error(slug: str) -> SlackApiError:
    return SlackApiError("The request to the Slack API failed.", {"ok": False, "error": slug})


def test_message_thread_ts_treats_empty_as_missing() -> None:
    assert _message_thread_ts(_msg("1.0")) is None
    assert _message_thread_ts(_msg("1.0", thread_ts="")) is None
    assert _message_thread_ts(_msg("1.0", thread_ts=PARENT)) == PARENT


def test_thread_root_ts_uses_nested_thread_ts() -> None:
    reply_only = _msg(REPLY_A, thread_ts=PARENT)
    assert _thread_root_ts([reply_only], REPLY_A) == PARENT
    assert _thread_root_ts([_msg(PARENT), _msg(REPLY_A)], REPLY_A) == PARENT
    assert _thread_root_ts([], REPLY_A) == REPLY_A


def test_history_thread_refetches_parent_when_replies_returns_reply_only() -> None:
    reply = _msg(REPLY_A, thread_ts=PARENT)
    parent = _msg(PARENT, thread_ts=PARENT)
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        side_effect=[[reply], [parent, reply]],
    ) as get_thread:
        thread = _history_thread(reply, MagicMock(), _channel())
    assert [call.kwargs["thread_id"] for call in get_thread.call_args_list] == [
        REPLY_A,
        PARENT,
    ]
    assert thread[0]["ts"] == PARENT


def test_history_thread_resolves_reply_without_thread_ts() -> None:
    reply = _msg(REPLY_A, text="TESTFACT reply 1")
    parent = _msg(PARENT, text="TESTFACT parent", thread_ts=PARENT)
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        return_value=[parent, reply, _msg(REPLY_B)],
    ) as get_thread:
        thread = _history_thread(reply, MagicMock(), _channel())
    get_thread.assert_called_once()
    assert get_thread.call_args.kwargs["thread_id"] == REPLY_A
    assert _thread_root_ts(thread, REPLY_A) == PARENT


def test_history_thread_falls_back_on_thread_not_found() -> None:
    reply = _msg(REPLY_A)
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        side_effect=_slack_error("thread_not_found"),
    ):
        thread = _history_thread(reply, MagicMock(), _channel())
    assert thread == [reply]


def test_history_thread_reraises_dead_token() -> None:
    reply = _msg(REPLY_A)
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        side_effect=_slack_error("invalid_auth"),
    ):
        with pytest.raises(SlackApiError):
            _history_thread(reply, MagicMock(), _channel())


def test_doc_id_ts_uses_parent_when_thread_ts_set() -> None:
    msg = _msg(REPLY_A, thread_ts=PARENT)
    client = MagicMock()
    assert _doc_id_ts_for_history_message(msg, client, _channel()) == PARENT
    client.fetch_thread_replies.assert_not_called()


def test_doc_id_ts_uses_replies_root_when_thread_ts_missing() -> None:
    reply = _msg(REPLY_A)
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        return_value=[_msg(PARENT), reply],
    ):
        assert _doc_id_ts_for_history_message(reply, MagicMock(), _channel()) == PARENT


def test_standalone_history_row_keeps_own_ts() -> None:
    msg = _msg("1789562804.694069", text="hi")
    with patch("onyx.connectors.slack.connector.get_thread", return_value=[msg]):
        assert _doc_id_ts_for_history_message(msg, MagicMock(), _channel()) == msg["ts"]


def test_message_to_doc_does_not_mint_reply_id() -> None:
    reply = _msg(REPLY_A, text="reply")
    parent = _msg(PARENT, text="parent")
    cleaner = MagicMock()
    cleaner.index_clean.side_effect = lambda text: text
    with (
        patch(
            "onyx.connectors.slack.connector.get_thread",
            return_value=[parent, reply],
        ),
        patch(
            "onyx.connectors.slack.connector.get_message_link",
            return_value="https://releasedesk.slack.com/archives/C0C1GG11S5D/p1",
        ),
        patch(
            "onyx.connectors.slack.connector.expert_info_from_slack_id",
            return_value=None,
        ),
    ):
        doc, reason, root = _message_to_doc(
            message=reply,
            slack_client=MagicMock(),
            channel=_channel(),
            slack_cleaner=cleaner,
            user_cache={},
            seen_thread_ts=set(),
            channel_access=None,
        )
    assert reason is None
    assert root == PARENT
    assert doc is not None
    assert doc.id == f"{CHANNEL_ID}__{PARENT}"


def test_message_to_doc_skips_when_parent_already_seen() -> None:
    reply = _msg(REPLY_A, text="reply")
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        return_value=[_msg(PARENT), reply],
    ) as get_thread:
        doc, reason, root = _message_to_doc(
            message=reply,
            slack_client=MagicMock(),
            channel=_channel(),
            slack_cleaner=MagicMock(),
            user_cache={},
            seen_thread_ts={PARENT},
            channel_access=None,
        )
    assert doc is None
    assert reason is None
    assert root == PARENT
    get_thread.assert_called_once()


def test_message_to_doc_skips_hinted_thread_without_fetch() -> None:
    msg = _msg(REPLY_A, thread_ts=PARENT)
    with patch("onyx.connectors.slack.connector.get_thread") as get_thread:
        doc, reason, root = _message_to_doc(
            message=msg,
            slack_client=MagicMock(),
            channel=_channel(),
            slack_cleaner=MagicMock(),
            user_cache={},
            seen_thread_ts={PARENT},
            channel_access=None,
        )
    get_thread.assert_not_called()
    assert doc is None
    assert reason is None
    assert root == PARENT


def test_message_to_doc_skips_reply_when_parent_thread_unavailable() -> None:
    history_row = _msg(REPLY_A)
    replies_payload = _msg(REPLY_A, thread_ts=PARENT)
    with patch(
        "onyx.connectors.slack.connector.get_thread",
        side_effect=[[replies_payload], _slack_error("thread_not_found")],
    ):
        doc, reason, root = _message_to_doc(
            message=history_row,
            slack_client=MagicMock(),
            channel=_channel(),
            slack_cleaner=MagicMock(),
            user_cache={},
            seen_thread_ts=set(),
            channel_access=None,
        )
    assert doc is None
    assert reason is None
    assert root == PARENT

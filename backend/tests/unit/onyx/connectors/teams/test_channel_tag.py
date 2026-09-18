"""Teams documents tag channel with the Graph display name (same key as Slack)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from onyx.connectors.teams.connector import (
    _channel_display_name,
    _convert_thread_to_document,
    _teams_author_display_name,
    _teams_message_speaker_name,
)
from onyx.connectors.teams.models import Body, From, Message, User


def _thread_message() -> Message:
    return Message(
        id="msg-1",
        replyToId=None,
        subject="Standup",
        from_=From(user=User(id="u1", display_name="Ada")),
        body=Body(content_type="html", content="<p>hello thread</p>"),
        created_date_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        last_modified_date_time=None,
        last_edited_date_time=None,
        deleted_date_time=None,
        web_url="https://teams.example/msg-1",
    )


def test_channel_display_name_uses_graph_display_name() -> None:
    channel = MagicMock()
    channel.properties = {"displayName": "Release Ops"}
    assert _channel_display_name(channel) == "Release Ops"


def test_channel_display_name_unknown_when_missing() -> None:
    channel = MagicMock()
    channel.properties = {}
    assert _channel_display_name(channel) == "Unknown"
    channel.properties = {"displayName": "  "}
    assert _channel_display_name(channel) == "Unknown"


def test_convert_thread_tags_channel_display_name() -> None:
    channel = MagicMock()
    channel.properties = {"displayName": "Release Ops"}
    with patch(
        "onyx.connectors.teams.connector.fetch_expert_infos", return_value=[]
    ), patch(
        "onyx.connectors.teams.connector.fetch_external_access",
        return_value=MagicMock(),
    ):
        doc = _convert_thread_to_document(MagicMock(), channel, [_thread_message()])
    assert doc is not None
    assert doc.metadata == {"channel": "Release Ops", "author": "Ada"}
    assert "id" not in doc.metadata
    assert "team" not in doc.metadata


def test_teams_author_omitted_when_unknown() -> None:
    unknown = _thread_message()
    unknown.from_ = From(user=User(id="u2", display_name="Unknown User"))
    assert _teams_author_display_name(unknown) is None
    missing = _thread_message()
    missing.from_ = None
    assert _teams_author_display_name(missing) is None


def _reply_message() -> Message:
    return Message(
        id="msg-2",
        replyToId="msg-1",
        subject=None,
        from_=From(user=User(id="u2", display_name="Bob")),
        body=Body(content_type="html", content="<p>ack</p>"),
        created_date_time=datetime(2024, 1, 15, 1, tzinfo=timezone.utc),
        last_modified_date_time=None,
        last_edited_date_time=None,
        deleted_date_time=None,
        web_url="https://teams.example/msg-2",
    )


def test_teams_message_speaker_none_when_missing() -> None:
    missing = _thread_message()
    missing.from_ = None
    assert _teams_message_speaker_name(missing) is None
    assert _teams_message_speaker_name(_thread_message()) == "Ada"


def test_convert_thread_prefixes_each_speaker() -> None:
    channel = MagicMock()
    channel.properties = {"displayName": "Release Ops"}
    with patch(
        "onyx.connectors.teams.connector.fetch_expert_infos", return_value=[]
    ), patch(
        "onyx.connectors.teams.connector.fetch_external_access",
        return_value=MagicMock(),
    ):
        doc = _convert_thread_to_document(
            MagicMock(), channel, [_thread_message(), _reply_message()]
        )
    assert doc is not None
    text = doc.sections[0].text
    assert "Ada: hello thread" in text
    assert "Bob: ack" in text


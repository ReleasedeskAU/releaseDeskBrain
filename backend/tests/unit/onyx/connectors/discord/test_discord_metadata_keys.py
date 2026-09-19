"""Discord document tags use lowercase keys, same as other connectors."""

from onyx.connectors.discord.metadata import discord_document_metadata


def test_text_channel_metadata_keys_are_lowercase() -> None:
    metadata = discord_document_metadata("releases", None)
    assert metadata == {"channel": "releases"}
    assert all(key == key.lower() for key in metadata)


def test_thread_metadata_keys_are_lowercase() -> None:
    metadata = discord_document_metadata(None, "cut-plan")
    assert metadata == {"thread": "cut-plan"}
    assert all(key == key.lower() for key in metadata)


def test_channel_and_thread_keys_are_both_lowercase() -> None:
    metadata = discord_document_metadata("releases", "cut-plan")
    assert metadata == {"channel": "releases", "thread": "cut-plan"}
    assert all(key == key.lower() for key in metadata)

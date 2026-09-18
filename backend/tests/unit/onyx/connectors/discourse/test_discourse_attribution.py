"""Discourse topic posts prefix each speaker."""

from onyx.connectors.discourse.connector import (
    _discourse_post_section_text,
    _discourse_speaker_name,
)


def test_discourse_speaker_prefers_name() -> None:
    assert _discourse_speaker_name({"name": "Ada", "username": "ada"}) == "Ada"
    assert _discourse_speaker_name({"username": "ada"}) == "ada"
    assert _discourse_speaker_name({}) is None
    assert _discourse_speaker_name({"name": "  "}) is None


def test_discourse_post_prefixes_speaker() -> None:
    text = _discourse_post_section_text({"name": "Ada", "cooked": "<p>hello</p>"})
    assert text.startswith("Ada:")
    assert "hello" in text


def test_discourse_post_unknown_when_missing() -> None:
    text = _discourse_post_section_text({"cooked": "<p>hello</p>"})
    assert text.startswith("Unknown:")
    assert "hello" in text

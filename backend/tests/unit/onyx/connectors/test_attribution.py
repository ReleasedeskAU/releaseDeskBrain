"""Shared speaker prefix and fold-site import allowlist."""

from pathlib import Path

from onyx.connectors.cross_connector_utils.attribution import (
    format_attributed_message,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[4]

# Grow this list when a later batch routes another fold through the helper.
_FOLD_SITES_REQUIRING_ATTRIBUTION = (
    "onyx/connectors/confluence/connector.py",
    "onyx/connectors/jira/utils.py",
    "onyx/connectors/linear/connector.py",
    "onyx/connectors/clickup/connector.py",
    "onyx/connectors/slack/connector.py",
    "onyx/connectors/teams/connector.py",
    "onyx/connectors/discourse/connector.py",
)


def test_format_attributed_message_known_name() -> None:
    assert format_attributed_message("Ada", "hello") == "Ada: hello"


def test_format_attributed_message_unknown_when_missing() -> None:
    assert format_attributed_message(None, "hello") == "Unknown: hello"
    assert format_attributed_message("", "hello") == "Unknown: hello"
    assert format_attributed_message("   ", "hello") == "Unknown: hello"


def test_fold_sites_import_format_attributed_message() -> None:
    missing: list[str] = []
    for relative in _FOLD_SITES_REQUIRING_ATTRIBUTION:
        source = (_BACKEND_ROOT / relative).read_text(encoding="utf-8")
        if (
            "from onyx.connectors.cross_connector_utils.attribution import"
            not in source
            or "format_attributed_message" not in source
        ):
            missing.append(relative)
    assert not missing, (
        "Fold sites must call format_attributed_message: " + ", ".join(missing)
    )


def test_slack_thread_sections_use_attributed_helper() -> None:
    source = (_BACKEND_ROOT / "onyx/connectors/slack/connector.py").read_text(
        encoding="utf-8"
    )
    assert "text=_attributed_slack_section_text(" in source
    assert 'text=slack_cleaner.index_clean(m["text"])' not in source


def test_teams_thread_text_uses_attributed_helper() -> None:
    source = (_BACKEND_ROOT / "onyx/connectors/teams/connector.py").read_text(
        encoding="utf-8"
    )
    assert "format_attributed_message(_teams_message_speaker_name(message)" in source


def test_discourse_posts_use_attributed_helper() -> None:
    source = (_BACKEND_ROOT / "onyx/connectors/discourse/connector.py").read_text(
        encoding="utf-8"
    )
    assert "text=_discourse_post_section_text(post)" in source

"""Test the OData filtering for MS Teams with special character handling."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from onyx.connectors.teams.connector import TeamsConnector, _collect_all_teams


def _mock_team(
    team_id: str,
    display_name: str | None,
    properties: dict[str, Any] | None = None,
) -> MagicMock:
    team = MagicMock()
    team.id = team_id
    team.display_name = display_name
    team.properties = properties if properties is not None else {}
    return team


def _mock_collection(teams: list[MagicMock], has_next: bool = False) -> MagicMock:
    collection = MagicMock()
    collection.has_next = has_next
    collection._next_request_url = (
        "https://graph.microsoft.com/v1.0/teams?$skiptoken=next" if has_next else None
    )
    collection.__iter__ = lambda self: iter(teams)  # noqa: ARG005
    return collection


def _mock_graph_client(
    collection: MagicMock | None = None,
    *,
    collections: list[MagicMock] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (graph_client, get_query, paged_query).

    ``paged_query`` is the object returned by both ``.top()`` and ``.filter()``.
    """
    mock_graph_client = MagicMock()
    mock_get_query = MagicMock()
    mock_paged_query = MagicMock()
    mock_paged_query.before_execute = MagicMock(return_value=mock_paged_query)

    pages = collections if collections is not None else [collection]
    mock_paged_query.execute_query.side_effect = pages
    mock_get_query.top.return_value = mock_paged_query
    mock_get_query.filter.return_value = mock_paged_query
    mock_graph_client.teams.get = MagicMock(return_value=mock_get_query)
    return mock_graph_client, mock_get_query, mock_paged_query


def test_special_characters_in_team_names() -> None:
    """Test that team names with special characters use client-side filtering."""
    mock_team = _mock_team("test-id", "Research & Development (R&D) Team")
    mock_graph_client, mock_get_query, _paged = _mock_graph_client(
        _mock_collection([mock_team])
    )

    # Test with team name containing special characters (has &, parentheses)
    # This should use client-side filtering (get().top()) instead of OData filtering
    result = _collect_all_teams(
        mock_graph_client, ["Research & Development (R&D) Team"]
    )

    # Verify that get().top() was called for client-side filtering
    mock_graph_client.teams.get.assert_called()
    mock_get_query.top.assert_called_with(50)
    mock_get_query.filter.assert_not_called()

    # Verify the team was found through client-side filtering
    assert len(result) == 1
    assert result[0].display_name == "Research & Development (R&D) Team"


def test_single_quote_escaping() -> None:
    """Test that team names with single quotes use OData filtering with proper escaping."""
    mock_team = _mock_team("quote-id", "Team's Group")
    mock_graph_client, mock_get_query, _paged = _mock_graph_client(
        _mock_collection([mock_team])
    )

    # Test with a team name containing a single quote (no &, (, ) so uses OData)
    result = _collect_all_teams(mock_graph_client, ["Team's Group"])

    # Verify OData filter was used (since no special characters)
    mock_graph_client.teams.get.assert_called()
    mock_get_query.filter.assert_called_once()
    mock_get_query.top.assert_not_called()

    # Verify the filter: single quote should be escaped to '' for OData syntax
    filter_arg = mock_get_query.filter.call_args[0][0]
    expected_filter = "displayName eq 'Team''s Group'"
    assert filter_arg == expected_filter, (
        f"Expected: {expected_filter}, Got: {filter_arg}"
    )
    assert len(result) == 1
    assert result[0].display_name == "Team's Group"


@pytest.mark.parametrize("requested", [None, []])
def test_blank_requested_fetches_all_teams(requested: list[str] | None) -> None:
    """Blank/None requested teams means index all accessible Graph teams."""
    alpha = _mock_team("id-alpha", "Alpha")
    beta = _mock_team("id-beta", "Beta")
    expired = _mock_team(
        "id-expired",
        "Expired",
        properties={"expirationDateTime": "2020-01-01T00:00:00Z"},
    )
    unnamed = _mock_team("id-unnamed", None)
    mock_graph_client, mock_get_query, _paged = _mock_graph_client(
        _mock_collection([alpha, beta, expired, unnamed])
    )

    result = _collect_all_teams(mock_graph_client, requested)

    mock_graph_client.teams.get.assert_called()
    mock_get_query.top.assert_called_with(50)
    mock_get_query.filter.assert_not_called()
    assert [team.display_name for team in result] == ["Alpha", "Beta"]


def test_blank_requested_paginates_all_teams() -> None:
    """Empty requested list must not stop after the first page."""
    page1_team = _mock_team("id-1", "First")
    page2_team = _mock_team("id-2", "Second")
    mock_graph_client, mock_get_query, mock_paged = _mock_graph_client(
        collections=[
            _mock_collection([page1_team], has_next=True),
            _mock_collection([page2_team], has_next=False),
        ]
    )

    result = _collect_all_teams(mock_graph_client, [])

    mock_get_query.filter.assert_not_called()
    mock_get_query.top.assert_called_with(50)
    assert mock_paged.execute_query.call_count == 2
    assert [team.display_name for team in result] == ["First", "Second"]


def test_named_team_uses_odata_filter() -> None:
    """Non-empty requested names still use OData eq filtering."""
    support = _mock_team("id-support", "Support")
    mock_graph_client, mock_get_query, _paged = _mock_graph_client(
        _mock_collection([support])
    )

    result = _collect_all_teams(mock_graph_client, ["Support"])

    mock_get_query.filter.assert_called_once_with("displayName eq 'Support'")
    mock_get_query.top.assert_not_called()
    assert len(result) == 1
    assert result[0].display_name == "Support"


def test_unmatched_requested_names_raise() -> None:
    """Non-empty requested names that match zero teams fail instead of indexing nothing."""
    mock_graph_client, _get_query, _paged = _mock_graph_client(_mock_collection([]))

    with pytest.raises(ValueError, match="No Teams found matching the requested names"):
        _collect_all_teams(mock_graph_client, ["Missing Team"])


def test_unmatched_special_char_names_raise() -> None:
    """Client-side special-char search also fails when nothing matches."""
    other = _mock_team("id-other", "Unrelated")
    mock_graph_client, mock_get_query, _paged = _mock_graph_client(
        _mock_collection([other])
    )

    with pytest.raises(ValueError, match="No Teams found matching the requested names"):
        _collect_all_teams(mock_graph_client, ["R&D"])

    mock_get_query.top.assert_called_with(50)
    mock_get_query.filter.assert_not_called()


def test_connector_blank_teams_config_is_empty_list() -> None:
    """UI/API blank teams (None or []) both become an empty requested list."""
    assert TeamsConnector().requested_team_list == []
    assert TeamsConnector(teams=None).requested_team_list == []
    assert TeamsConnector(teams=[]).requested_team_list == []


def test_helper_functions() -> None:
    """Test the helper functions for team name processing."""
    from onyx.connectors.teams.connector import (
        _can_use_odata_filter,
        _escape_odata_string,
        _has_odata_incompatible_chars,
    )

    # Test OData string escaping
    assert _escape_odata_string("Team's Group") == "Team''s Group"
    assert _escape_odata_string("Normal Team") == "Normal Team"

    # Test special character detection
    assert _has_odata_incompatible_chars(["R&D Team"])
    assert _has_odata_incompatible_chars(["Team (Alpha)"])
    assert not _has_odata_incompatible_chars(["Normal Team"])
    assert not _has_odata_incompatible_chars([])
    assert not _has_odata_incompatible_chars(None)

    # Test filtering strategy determination
    can_use, safe, problematic = _can_use_odata_filter(["Normal Team", "R&D Team"])
    assert can_use
    assert "Normal Team" in safe
    assert "R&D Team" in problematic

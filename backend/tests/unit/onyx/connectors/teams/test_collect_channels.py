"""Skip Teams that Graph lists but that have no channels resource."""

from unittest.mock import MagicMock, patch

import pytest
from office365.runtime.client_request_exception import ClientRequestException

from onyx.connectors.teams.connector import (
    _collect_all_channels_from_team,
    _load_team_or_skip,
)


def _graph_error(status_code: int) -> ClientRequestException:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    response.content = b""
    return ClientRequestException("error", response=response)


def test_collect_channels_skips_graph_404() -> None:
    """A listed team with no channel thread must not fail the connector."""
    team = MagicMock()
    team.id = "missing-thread-team"
    team.channels.get_all.return_value = MagicMock()

    with patch(
        "onyx.connectors.teams.connector.execute_query_with_retry",
        side_effect=_graph_error(404),
    ):
        assert _collect_all_channels_from_team(team) == []


def test_collect_channels_reraises_forbidden() -> None:
    """401/403 are credential/permission failures — do not skip."""
    team = MagicMock()
    team.id = "forbidden-team"
    team.channels.get_all.return_value = MagicMock()

    with patch(
        "onyx.connectors.teams.connector.execute_query_with_retry",
        side_effect=_graph_error(403),
    ):
        with pytest.raises(ClientRequestException):
            _collect_all_channels_from_team(team)


def test_load_team_or_skip_returns_none_on_404() -> None:
    with patch(
        "onyx.connectors.teams.connector._get_team_by_id",
        side_effect=_graph_error(404),
    ):
        assert _load_team_or_skip(MagicMock(), "gone-team") is None


def test_load_team_or_skip_reraises_unauthorized() -> None:
    with patch(
        "onyx.connectors.teams.connector._get_team_by_id",
        side_effect=_graph_error(401),
    ):
        with pytest.raises(ClientRequestException):
            _load_team_or_skip(MagicMock(), "expired-token-team")

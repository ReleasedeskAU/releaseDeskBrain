from unittest.mock import MagicMock

import pytest

from onyx.connectors.bitbucket.connector import BitbucketConnector
from onyx.connectors.exceptions import (
    CredentialExpiredError,
    UnexpectedValidationError,
)


def _connector(repositories: str | None = "website-test") -> BitbucketConnector:
    connector = BitbucketConnector(workspace="testing-connector", repositories=repositories)
    connector.load_credentials(
        {"bitbucket_email": "owner@example.com", "bitbucket_api_token": "tok"}
    )
    return connector


def _client_returning(status: int, captured: list[str]) -> MagicMock:
    response = MagicMock(status_code=status)
    inner = MagicMock()
    inner.get.side_effect = lambda url, **_kwargs: (captured.append(url) or response)
    context = MagicMock()
    context.__enter__.return_value = inner
    context.__exit__.return_value = False
    return context


def test_validation_url_uses_first_repo_not_workspace_list() -> None:
    connector = _connector("website-test,other")
    assert connector._validation_url() == (
        "https://api.bitbucket.org/2.0/repositories/testing-connector/website-test"
    )


def test_validation_url_uses_workspace_resource_when_no_repos() -> None:
    connector = _connector(None)
    assert connector._validation_url() == (
        "https://api.bitbucket.org/2.0/workspaces/testing-connector"
    )


def test_validate_accepts_reachable_repo() -> None:
    connector = _connector()
    captured: list[str] = []
    connector._client = lambda: _client_returning(200, captured)  # type: ignore[method-assign]
    connector.validate_connector_settings()
    assert captured == [connector._validation_url()]


def test_validate_maps_401() -> None:
    connector = _connector()
    connector._client = lambda: _client_returning(401, [])  # type: ignore[method-assign]
    with pytest.raises(CredentialExpiredError, match="HTTP 401"):
        connector.validate_connector_settings()


def test_validate_maps_404() -> None:
    connector = _connector()
    connector._client = lambda: _client_returning(404, [])  # type: ignore[method-assign]
    with pytest.raises(UnexpectedValidationError, match="status=404"):
        connector.validate_connector_settings()

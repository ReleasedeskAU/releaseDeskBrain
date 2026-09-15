from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from onyx.connectors.exceptions import InsufficientPermissionsError
from onyx.connectors.github.scopes import (
    accepted_permissions_include_contents_read,
    assert_github_client_repo_read,
    github_contents_404_is_empty_repo,
    parse_oauth_scopes,
)


def test_parse_oauth_scopes_accepts_repo() -> None:
    assert "repo" in parse_oauth_scopes("repo, gist")
    assert parse_oauth_scopes("") == []
    assert parse_oauth_scopes(None) == []


def test_accepted_permissions_include_contents_read() -> None:
    assert accepted_permissions_include_contents_read("metadata=read;contents=read") is True
    assert accepted_permissions_include_contents_read("metadata=read") is False


def test_classic_token_missing_repo_scope() -> None:
    client = MagicMock()
    client.requester.requestJsonAndCheck.return_value = (
        {"X-OAuth-Scopes": ""},
        [{"full_name": "acme/app"}],
    )
    with pytest.raises(InsufficientPermissionsError, match="scope: repo"):
        assert_github_client_repo_read(client)


def test_classic_token_with_repo_scope() -> None:
    client = MagicMock()
    client.requester.requestJsonAndCheck.return_value = (
        {"X-OAuth-Scopes": "repo"},
        [{"full_name": "acme/app"}],
    )
    assert_github_client_repo_read(client)


def test_fine_grained_missing_contents_read() -> None:
    client = MagicMock()
    client.requester.requestJsonAndCheck.side_effect = [
        ({"X-Accepted-GitHub-Permissions": "metadata=read"}, [{"full_name": "acme/app"}]),
        GithubException(403, {"message": "Resource not accessible by personal access token"}, {}),
    ]
    with pytest.raises(InsufficientPermissionsError, match="Contents \\(read\\)"):
        assert_github_client_repo_read(client)


def test_github_contents_404_is_empty_repo() -> None:
    assert github_contents_404_is_empty_repo({"message": "This repository is empty."}) is True
    assert github_contents_404_is_empty_repo({"message": "Not Found"}) is False
    assert github_contents_404_is_empty_repo(None) is False


def test_fine_grained_contents_404_not_found() -> None:
    client = MagicMock()
    client.requester.requestJsonAndCheck.side_effect = [
        ({"X-Accepted-GitHub-Permissions": "metadata=read"}, [{"full_name": "acme/app"}]),
        GithubException(404, {"message": "Not Found"}, {}),
    ]
    with pytest.raises(InsufficientPermissionsError, match="Contents \\(read\\)"):
        assert_github_client_repo_read(client)


def test_fine_grained_empty_repo_contents_404() -> None:
    client = MagicMock()
    client.requester.requestJsonAndCheck.side_effect = [
        ({"X-Accepted-GitHub-Permissions": "metadata=read"}, [{"full_name": "acme/app"}]),
        GithubException(404, {"message": "This repository is empty."}, {}),
    ]
    assert_github_client_repo_read(client)

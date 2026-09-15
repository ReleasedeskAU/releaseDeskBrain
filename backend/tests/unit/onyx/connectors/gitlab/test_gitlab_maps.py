from types import SimpleNamespace

from onyx.connectors.gitlab.docs import (
    map_commit_to_document,
    map_overview_to_document,
    map_readme_to_document,
)


def test_map_overview_readme_and_commit() -> None:
    overview = map_overview_to_document(
        "acme/app",
        "App",
        "Release tools",
        "main",
        "https://gitlab.com/acme/app",
        "private",
    )
    assert overview.metadata["object_type"] == "Repository"
    assert "Release tools" in overview.sections[0].text
    assert overview.id.endswith(":overview")

    readme = map_readme_to_document(
        "acme/app", "README.md", "# Hello", "main", "https://gitlab.com/acme/app"
    )
    assert readme.metadata["object_type"] == "Readme"
    assert readme.sections[0].text == "# Hello"

    commit = map_commit_to_document(
        SimpleNamespace(
            id="abc123def",
            message="Fix login\n\nDetails",
            title="Fix login",
            author_name="Ada",
            authored_date="2026-03-01T12:00:00.000Z",
            web_url="https://gitlab.com/acme/app/-/commit/abc123def",
        ),
        "acme/app",
        "https://gitlab.com/acme/app",
    )
    assert commit.metadata["object_type"] == "Commit"
    assert "Fix login" in commit.sections[0].text
    assert "diff" not in commit.sections[0].text.lower()
    assert commit.id.endswith(":commit:abc123def")

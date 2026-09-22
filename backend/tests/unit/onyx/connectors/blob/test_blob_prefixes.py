import pytest

from onyx.connectors.blob.prefixes import resolve_blob_prefixes


def test_legacy_prefix_still_normalizes() -> None:
    assert resolve_blob_prefixes(None, "docs") == ["docs/"]
    assert resolve_blob_prefixes(None, "docs/") == ["docs/"]


def test_legacy_empty_prefix_unchanged() -> None:
    assert resolve_blob_prefixes(None, "") == [""]


def test_prefixes_list_preferred_over_legacy_prefix() -> None:
    assert resolve_blob_prefixes(["docs/", "images"], "old/") == ["docs/", "images/"]


def test_empty_prefixes_list_rejected() -> None:
    with pytest.raises(ValueError, match="at least one folder"):
        resolve_blob_prefixes([], "")
    with pytest.raises(ValueError, match="at least one folder"):
        resolve_blob_prefixes(["", "   "], "legacy/")
    with pytest.raises(ValueError, match="list of folder paths"):
        resolve_blob_prefixes("docs/", "")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="list of folder paths"):
        resolve_blob_prefixes([1], "")  # type: ignore[list-item]


def test_prefixes_dedupe_preserve_order() -> None:
    assert resolve_blob_prefixes(["docs/", "docs", "images/"], "") == [
        "docs/",
        "images/",
    ]

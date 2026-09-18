"""TEI rerank: hybrid-only, silent fallback, no query/passage in logs."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from onyx.configs.constants import DocumentSource
from onyx.context.search.models import SearchDoc
from onyx.server.query_and_chat.tei_rerank import (
    maybe_rerank_hybrid_documents,
    should_rerank_admin_search,
)


def _doc(doc_id: str, blurb: str) -> SearchDoc:
    return SearchDoc(
        document_id=doc_id,
        chunk_ind=0,
        semantic_identifier=doc_id,
        blurb=blurb,
        source_type=DocumentSource.JIRA,
        boost=0,
        hidden=False,
        metadata={},
        match_highlights=[],
    )


def test_should_rerank_requires_flag_hybrid_and_query() -> None:
    with patch("onyx.server.query_and_chat.tei_rerank.ENABLE_RERANK", False):
        assert should_rerank_admin_search("hybrid", "release") is False
    with patch("onyx.server.query_and_chat.tei_rerank.ENABLE_RERANK", True):
        assert should_rerank_admin_search("hybrid", "release") is True
        assert should_rerank_admin_search("keyword", "release") is False
        assert should_rerank_admin_search("hybrid", "  ") is False


def test_flag_off_does_not_call_tei() -> None:
    docs = [_doc("a", "alpha"), _doc("b", "beta")]
    with (
        patch("onyx.server.query_and_chat.tei_rerank.ENABLE_RERANK", False),
        patch("onyx.server.query_and_chat.tei_rerank.urlopen") as opener,
    ):
        out = maybe_rerank_hybrid_documents("when is the release", docs)
    assert out is docs
    opener.assert_not_called()


def test_success_keeps_top_ten_in_score_order() -> None:
    docs = [_doc(f"d{i}", f"passage {i}") for i in range(12)]
    payload = [{"index": i, "score": float(i)} for i in range(12)]
    response = MagicMock()
    response.status = 200
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with (
        patch("onyx.server.query_and_chat.tei_rerank.ENABLE_RERANK", True),
        patch(
            "onyx.server.query_and_chat.tei_rerank.urlopen", return_value=response
        ) as opener,
    ):
        out = maybe_rerank_hybrid_documents("when is the release", docs)
    opener.assert_called_once()
    assert [doc.document_id for doc in out] == [f"d{i}" for i in range(11, 1, -1)]
    assert len(out) == 10


def test_timeout_returns_original_hybrid_list() -> None:
    docs = [_doc("a", "alpha"), _doc("b", "beta")]
    with (
        patch("onyx.server.query_and_chat.tei_rerank.ENABLE_RERANK", True),
        patch(
            "onyx.server.query_and_chat.tei_rerank.urlopen",
            side_effect=URLError("timed out"),
        ),
        patch("onyx.server.query_and_chat.tei_rerank.logger.warning") as warn,
    ):
        out = maybe_rerank_hybrid_documents("SECRET_QUERY_TEXT", docs)
    assert out is docs
    warn.assert_called_once()
    joined = " ".join(str(arg) for arg in warn.call_args.args)
    assert "SECRET_QUERY_TEXT" not in joined
    assert "alpha" not in joined
    assert "URLError" in joined
    assert "hybrid order" in joined


def test_bad_json_returns_original_list() -> None:
    docs = [_doc("a", "alpha")]
    response = MagicMock()
    response.status = 200
    response.read.return_value = b"not-json"
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with (
        patch("onyx.server.query_and_chat.tei_rerank.ENABLE_RERANK", True),
        patch("onyx.server.query_and_chat.tei_rerank.urlopen", return_value=response),
    ):
        assert maybe_rerank_hybrid_documents("q", docs) is docs

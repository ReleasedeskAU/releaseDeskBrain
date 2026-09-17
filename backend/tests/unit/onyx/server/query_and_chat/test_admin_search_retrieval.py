"""Admin search retrieval: keyword default, hybrid opt-in, empty query stays random."""

from unittest.mock import MagicMock, patch

from onyx.context.search.enums import QueryType
from onyx.context.search.models import BaseFilters, IndexFilters
from onyx.server.query_and_chat.models import AdminSearchRequest
from onyx.server.query_and_chat.query_backend import retrieve_admin_search_chunks


def test_admin_search_request_defaults_to_keyword() -> None:
    body = AdminSearchRequest(query="release", filters=BaseFilters())
    assert body.retrieval == "keyword"


def test_admin_search_request_accepts_hybrid() -> None:
    body = AdminSearchRequest(query="release", filters=BaseFilters(), retrieval="hybrid")
    assert body.retrieval == "hybrid"


def test_empty_query_uses_random_even_when_hybrid_requested() -> None:
    document_index = MagicMock()
    filters = IndexFilters(access_control_list=None)
    retrieve_admin_search_chunks(
        query="  ",
        retrieval="hybrid",
        document_index=document_index,
        filters=filters,
        db_session=MagicMock(),
    )
    document_index.random_retrieval.assert_called_once_with(filters=filters)
    document_index.hybrid_retrieval.assert_not_called()
    document_index.keyword_retrieval.assert_not_called()


def test_default_keyword_path_does_not_embed() -> None:
    document_index = MagicMock()
    filters = IndexFilters(access_control_list=None)
    with patch(
        "onyx.server.query_and_chat.query_backend.get_query_embedding"
    ) as embed:
        retrieve_admin_search_chunks(
            query="BN-15 children",
            retrieval="keyword",
            document_index=document_index,
            filters=filters,
            db_session=MagicMock(),
        )
    embed.assert_not_called()
    document_index.keyword_retrieval.assert_called_once()
    kwargs = document_index.keyword_retrieval.call_args.kwargs
    assert kwargs["include_hidden"] is True
    document_index.hybrid_retrieval.assert_not_called()


def test_hybrid_path_embeds_and_passes_include_hidden_true() -> None:
    document_index = MagicMock()
    filters = IndexFilters(access_control_list=None)
    db_session = MagicMock()
    with patch(
        "onyx.server.query_and_chat.query_backend.get_query_embedding",
        return_value=[0.1, 0.2],
    ) as embed:
        retrieve_admin_search_chunks(
            query="what is BN-15 about",
            retrieval="hybrid",
            document_index=document_index,
            filters=filters,
            db_session=db_session,
        )
    embed.assert_called_once_with("what is BN-15 about", db_session=db_session)
    document_index.hybrid_retrieval.assert_called_once()
    kwargs = document_index.hybrid_retrieval.call_args.kwargs
    assert kwargs["include_hidden"] is True
    assert kwargs["query_type"] == QueryType.SEMANTIC
    assert kwargs["query_embedding"] == [0.1, 0.2]
    document_index.keyword_retrieval.assert_not_called()
    document_index.random_retrieval.assert_not_called()

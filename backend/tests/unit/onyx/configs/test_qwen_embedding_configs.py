"""Qwen3-Embedding-0.6B catalog and required query prefix."""

from onyx.configs.embedding_configs import (
    QWEN3_EMBEDDING_0_6B,
    QWEN3_EMBEDDING_0_6B_DIM,
    QWEN3_EMBEDDING_QUERY_PREFIX,
    SUPPORTED_EMBEDDING_MODELS,
    default_embedding_prefixes,
)


def test_supported_embedding_models_include_qwen3_0_6b() -> None:
    rows = [
        model for model in SUPPORTED_EMBEDDING_MODELS if model.name == QWEN3_EMBEDDING_0_6B
    ]
    assert len(rows) == 2
    assert {model.dim for model in rows} == {QWEN3_EMBEDDING_0_6B_DIM}


def test_qwen3_query_prefix_matches_published_prompt() -> None:
    query_prefix, passage_prefix = default_embedding_prefixes(QWEN3_EMBEDDING_0_6B)
    assert query_prefix == QWEN3_EMBEDDING_QUERY_PREFIX
    assert query_prefix.startswith("Instruct:")
    assert query_prefix.endswith("Query:")
    assert passage_prefix == ""


def test_unknown_local_model_gets_no_invented_prefix() -> None:
    assert default_embedding_prefixes("some-org/unknown-embed") == ("", "")

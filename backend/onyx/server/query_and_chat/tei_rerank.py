"""TEI rerank for admin hybrid search. Failures return hybrid order unchanged."""

from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from onyx.configs.app_configs import (
    ENABLE_RERANK,
    RERANK_CANDIDATES,
    RERANK_KEEP,
    RERANK_TIMEOUT_SECONDS,
    RERANKER_URL,
)
from onyx.context.search.models import SearchDoc
from onyx.utils.logger import setup_logger

logger = setup_logger()

# TEI bge-reranker-base: max_input_length=512 tokens, auto_truncate=false.
# 2000-char texts 413; 1512 'x' chars is the measured per-text 200 ceiling.
_MAX_PASSAGE_CHARS = 1512


def should_rerank_admin_search(retrieval: str, query: str) -> bool:
    """True only for a non-empty hybrid Ask search with ENABLE_RERANK on.

    Keyword and empty-query (random sample) never rerank.
    """
    return ENABLE_RERANK and retrieval == "hybrid" and bool(query.strip())


def maybe_rerank_hybrid_documents(
    query: str, documents: list[SearchDoc]
) -> list[SearchDoc]:
    """Reorder unique hybrid hits via TEI. On any failure return ``documents``.

    Takes up to RERANK_CANDIDATES passages, returns RERANK_KEEP. Skips TEI when
    ENABLE_RERANK is off. Does not log query or passage text. Does not raise.
    """
    if not ENABLE_RERANK or not documents:
        return documents
    candidates: list[SearchDoc] = []
    texts: list[str] = []
    for doc in documents[:RERANK_CANDIDATES]:
        text = _passage_text(doc)
        if not text:
            continue
        candidates.append(doc)
        texts.append(text)
    if not texts:
        return documents
    ranked = _tei_rerank_indices(query, texts)
    if ranked is None:
        return documents
    out: list[SearchDoc] = []
    seen: set[int] = set()
    for index in ranked:
        if index in seen or index < 0 or index >= len(candidates):
            continue
        seen.add(index)
        out.append(candidates[index])
        if len(out) >= RERANK_KEEP:
            break
    return out or documents


def _passage_text(doc: SearchDoc) -> str:
    """Blurb, else title. Empty string means the doc is not sent to TEI."""
    raw = (doc.blurb or "").strip() or (doc.semantic_identifier or "").strip()
    return raw[:_MAX_PASSAGE_CHARS]


def _tei_rerank_indices(query: str, texts: list[str]) -> list[int] | None:
    """POST /rerank. Returns indices by score descending, or None on failure.

    Logs only the exception class name — never query, texts, or response body.
    """
    payload = json.dumps({"query": query, "texts": texts}).encode()
    request = Request(
        f"{RERANKER_URL}/rerank",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=RERANK_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                logger.warning(
                    "TEI rerank failed (HTTP %s); using hybrid order",
                    response.status,
                )
                return None
            raw = response.read()
        parsed = json.loads(raw.decode())
    except (URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning(
            "TEI rerank failed (%s); using hybrid order",
            type(exc).__name__,
        )
        return None
    return _ranked_indices_from_tei(parsed)


def _ranked_indices_from_tei(parsed: object) -> list[int] | None:
    """Parse TEI ``[{index, score}, ...]`` into score-descending indices."""
    if not isinstance(parsed, list) or not parsed:
        logger.warning("TEI rerank failed (empty); using hybrid order")
        return None
    scored: list[tuple[float, int]] = []
    for item in parsed:
        if not isinstance(item, dict):
            logger.warning("TEI rerank failed (shape); using hybrid order")
            return None
        index = item.get("index")
        score = item.get("score")
        if not isinstance(index, int) or not isinstance(score, (int, float)):
            logger.warning("TEI rerank failed (shape); using hybrid order")
            return None
        scored.append((float(score), index))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [index for _score, index in scored]

"""Regression: D4as_v5 overlay must not default to 2g + 6×16 in-flight batches.

Celery workers were MEMCG-OOM-killed under BACKGROUND_MEM_LIMIT=2g with stock
INDEX_BATCH_SIZE=16 and CELERY_WORKER_DOCPROCESSING_CONCURRENCY=6.
"""

from __future__ import annotations

import unittest
from pathlib import Path

OVERLAY = Path(__file__).resolve().parents[1]
COMPOSE = OVERLAY / "docker-compose.releasedesk.yml"
ENV_EXAMPLE = OVERLAY / "env.onyx.d4s-v5.example"


class IndexingMemoryDefaultsTest(unittest.TestCase):
    def test_compose_caps_batch_and_docprocessing_concurrency(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("INDEX_BATCH_SIZE=${INDEX_BATCH_SIZE:-4}", text)
        self.assertIn(
            "CELERY_WORKER_DOCPROCESSING_CONCURRENCY=${CELERY_WORKER_DOCPROCESSING_CONCURRENCY:-2}",
            text,
        )
        self.assertIn("BACKGROUND_MEM_LIMIT:-4g", text)
        self.assertNotIn("BACKGROUND_MEM_LIMIT:-2g", text)

    def test_env_example_matches_compose_defaults(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("BACKGROUND_MEM_LIMIT=4g", text)
        self.assertIn("INDEX_BATCH_SIZE=4", text)
        self.assertIn("CELERY_WORKER_DOCPROCESSING_CONCURRENCY=2", text)
        self.assertNotIn("BACKGROUND_MEM_LIMIT=2g", text)
        self.assertIn("RERANKER_MEM_LIMIT=5g", text)
        self.assertIn("RERANKER_CPU_LIMIT=2.0", text)

    def test_reranker_is_internal_tei_with_5g_cap(self) -> None:
        """CPU TEI reranker: pinned image, 5g / 2 CPU, no host port, no api depends_on."""
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertIn(
            "image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.8.2",
            text,
        )
        self.assertIn("BAAI/bge-reranker-base", text)
        self.assertIn("RERANKER_MEM_LIMIT:-5g", text)
        self.assertIn("RERANKER_CPU_LIMIT:-2.0", text)
        self.assertIn("reranker_model_cache:/data", text)
        self.assertNotRegex(text, r"reranker:[\s\S]*?ports:")
        self.assertNotIn("depends_on:\n      reranker", text)
        api_block = text.split("  api_server:", 1)[1].split("\n  background:", 1)[0]
        self.assertNotIn("reranker", api_block)


if __name__ == "__main__":
    unittest.main()

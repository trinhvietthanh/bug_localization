"""
BM25 index for hybrid semantic + keyword search.

Builds a file-level BM25 index from the documents already stored in Qdrant,
so no extra file I/O is needed. The index is built lazily and cached per
(repo_filter, language_filter) key.

Tokenisation splits on:
  - camelCase  (TypeInference → type, inference)
  - PascalCase (FlowSensitiveInline → flow, sensitive, inline)
  - snake_case  (parse_pattern → parse, pattern)
  - dots/slashes (org.jfree.chart → org, jfree, chart)
  - digits kept separately

This gives BM25 a strong signal on identifier names, which are exactly
the tokens that differentiate sibling files (e.g. TypeCheck vs TypeInference).
"""

import re
import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tokenisation ──────────────────────────────────────────────────────────────

_CAMEL_RE = re.compile(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]|\d+')


def tokenize(text: str) -> list[str]:
    """
    Tokenise source code / query text for BM25.

    Strategy:
    1. Split on non-alphanumeric boundaries first
    2. For each resulting token, further split on camelCase/PascalCase
    3. Lower-case everything and drop single-char tokens
    """
    tokens: list[str] = []
    raw = re.split(r'[\s/\\.,()\[\]{}<>:;\'"`@#$%^&*+=|!?~\-]+', text)
    for part in raw:
        if not part:
            continue
        tokens.append(part.lower())
        sub = _CAMEL_RE.findall(part)
        if len(sub) > 1:
            tokens.extend(t.lower() for t in sub)
    return [t for t in tokens if len(t) > 1]


# ── BM25 index ────────────────────────────────────────────────────────────────

class BM25FileIndex:
    """
    File-level BM25 index built from Qdrant chunk documents.

    Each "document" in the BM25 corpus is the concatenation of all chunk
    texts belonging to one file.  This gives whole-file BM25 scores.
    """

    def __init__(self):
        from rank_bm25 import BM25Okapi
        self._BM25Okapi = BM25Okapi

        self.file_paths: list[str] = []
        self.corpus_tokens: list[list[str]] = []
        self._bm25 = None

    def build_from_qdrant(
        self,
        client,
        collection_name: str,
        repo_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
    ) -> int:
        """
        Build (or rebuild) the index from a Qdrant collection.

        Scrolls all chunks matching the filters, aggregates text per file,
        tokenises, and builds the BM25Okapi index.

        Returns the number of files indexed.
        """
        from qdrant_client import models

        conditions = []
        if repo_filter:
            conditions.append(models.FieldCondition(
                key="repo_id", match=models.MatchValue(value=repo_filter)
            ))
        if language_filter:
            conditions.append(models.FieldCondition(
                key="language", match=models.MatchValue(value=language_filter)
            ))
        qdrant_filter = models.Filter(must=conditions) if conditions else None

        file_texts: dict[str, list[str]] = defaultdict(list)
        offset = None
        total_fetched = 0

        while True:
            try:
                records, offset = client.scroll(
                    collection_name=collection_name,
                    scroll_filter=qdrant_filter,
                    limit=1000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as exc:
                logger.error(f"BM25: Qdrant scroll failed: {exc}")
                break

            for rec in records:
                fp = (rec.payload or {}).get("file_path", "")
                text = (rec.payload or {}).get("text", "")
                if fp and text:
                    file_texts[fp].append(text)
            total_fetched += len(records)

            if offset is None:
                break

        if not file_texts:
            logger.warning("BM25: no documents found in collection")
            return 0

        self.file_paths = sorted(file_texts.keys())
        self.corpus_tokens = [
            tokenize(" ".join(file_texts[fp]))
            for fp in self.file_paths
        ]
        self._bm25 = self._BM25Okapi(self.corpus_tokens)
        logger.info(f"BM25: indexed {len(self.file_paths)} files ({total_fetched} chunks scrolled)")
        return len(self.file_paths)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """
        Return top-k (file_path, bm25_score) sorted by score descending.
        Scores are normalised to [0, 1] by dividing by the maximum score.
        """
        if self._bm25 is None or not self.file_paths:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)

        max_score = float(scores.max()) if scores.max() > 0 else 1.0
        ranked = sorted(
            zip(self.file_paths, (float(s) / max_score for s in scores)),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_k]


# ── Module-level cache ────────────────────────────────────────────────────────

_index_cache: dict[tuple, BM25FileIndex] = {}


def get_or_build_index(
    client,
    collection_name: str,
    repo_filter: Optional[str] = None,
    language_filter: Optional[str] = None,
) -> Optional[BM25FileIndex]:
    """Return a cached BM25FileIndex, building it from Qdrant if necessary."""
    key = (repo_filter, language_filter)
    if key not in _index_cache:
        idx = BM25FileIndex()
        n = idx.build_from_qdrant(client, collection_name, repo_filter, language_filter)
        if n == 0:
            return None
        _index_cache[key] = idx
    return _index_cache[key]



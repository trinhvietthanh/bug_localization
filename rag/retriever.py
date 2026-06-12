"""
Semantic retriever for the RAG pipeline.
Queries the Qdrant vector store to find relevant code.

Enhanced for Ground-truth File Identification (GFI):
- package_filter for Java package-scoped queries
- language_filter for cross-language disambiguation
- get_similar_files returns package_name and language metadata
- get_file_summaries returns file_summary chunks for fast structural lookup
- hybrid_get_similar_files: BM25 + semantic RRF fusion
"""

import logging
from collections import OrderedDict
from typing import Optional

from rag.embedder import CodeEmbedder

logger = logging.getLogger(__name__)

_EMBED_CACHE_MAX = 1000


class CodeRetriever:
    """Retrieves code chunks from Qdrant based on semantic similarity."""

    def __init__(
        self,
        persist_directory: str,
        collection_name: str = "codebase",
        embedding_model: str = "all-MiniLM-L6-v2",
        embedding_api_base: str = "",
        embedding_api_key: str = "",
    ):
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.embedder = CodeEmbedder(
            model_name=embedding_model,
            api_base=embedding_api_base,
            api_key=embedding_api_key,
        )
        self._embed_cache: OrderedDict[str, list[float]] = OrderedDict()

        from qdrant_client import QdrantClient
        self.client = QdrantClient(path=persist_directory)

        # Verify collection exists and has data
        try:
            info = self.client.get_collection(collection_name)
            if info.points_count == 0:
                logger.warning(
                    f"Collection '{collection_name}' is empty. Run indexing first."
                )
            else:
                logger.debug(
                    f"Collection '{collection_name}' ready: {info.points_count} chunks"
                )
            self._ready = True
        except Exception:
            logger.warning(
                f"Collection '{collection_name}' not found. Run indexing first."
            )
            self._ready = False

    # ------------------------------------------------------------------
    # Core query
    # ------------------------------------------------------------------

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        min_score: float = 0.0,
        file_filter: Optional[str] = None,
        chunk_type_filter: Optional[str] = None,
        repo_filter: Optional[str] = None,
        package_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
    ) -> list[tuple[dict, float]]:
        """
        Query the vector store for relevant code chunks.

        Args:
            query_text:        Natural language or code query
            top_k:             Number of results to return
            min_score:         Minimum similarity score threshold (default 0.0, no filter)
            file_filter:       Substring match on file_path
            chunk_type_filter: Exact match on chunk_type
            repo_filter:       Exact match on repo_id (use project name, e.g. "Lang")
            package_filter:    Substring / text match on package_name (Java)
            language_filter:   Exact match on language ("java", "python", …)

        Returns:
            List of (document_dict, similarity_score) tuples.
            document_dict has 'text' and 'metadata' keys.
            Scores are cosine similarities in [−1, 1] (higher = more similar).
        """
        if not self._ready:
            logger.error("No collection available. Run indexing first.")
            return []

        qdrant_filter = self._build_filter(
            repo_filter=repo_filter,
            language_filter=language_filter,
            chunk_type_filter=chunk_type_filter,
            package_filter=package_filter,
            file_filter=file_filter,
        )

        if query_text in self._embed_cache:
            query_embedding = self._embed_cache[query_text]
            self._embed_cache.move_to_end(query_text)
        else:
            query_embedding = self.embedder.embed_text(query_text)
            if len(self._embed_cache) >= _EMBED_CACHE_MAX:
                self._embed_cache.popitem(last=False)
            self._embed_cache[query_text] = query_embedding

        try:
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                query_filter=qdrant_filter,
                limit=top_k,
                with_payload=True,
            )
        except Exception as e:
            logger.error(f"Qdrant query failed: {e}")
            return []

        output = []
        for hit in hits:
            if hit.score < min_score:
                continue
            payload = dict(hit.payload)
            text = payload.pop("text", "")
            output.append(({"text": text, "metadata": payload}, hit.score))

        return output

    # ------------------------------------------------------------------
    # File-level helpers
    # ------------------------------------------------------------------

    def get_similar_files(
        self,
        query_text: str,
        top_k: int = 20,
        min_score: float = 0.0,
        repo_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
        include_summaries_only: bool = False,
    ) -> list[dict]:
        """
        Get unique files ranked by their best chunk similarity score.

        Prioritises file_summary chunks and de-duplicates by file path.
        """
        chunk_type = "file_summary" if include_summaries_only else None
        fetch_k = min(top_k * 3, top_k + 60)

        results = self.query(
            query_text,
            top_k=fetch_k,
            min_score=min_score,
            repo_filter=repo_filter,
            chunk_type_filter=chunk_type,
            language_filter=language_filter,
        )

        if not results and include_summaries_only:
            results = self.query(
                query_text,
                top_k=fetch_k,
                min_score=min_score,
                repo_filter=repo_filter,
                language_filter=language_filter,
            )

        file_best: dict[str, dict] = {}
        for doc, score in results:
            meta = doc["metadata"]
            fp = meta.get("file_path", "")
            if not fp:
                continue
            if fp not in file_best or score > file_best[fp]["score"]:
                file_best[fp] = {
                    "file_path": fp,
                    "score": score,
                    "package_name": meta.get("package_name", ""),
                    "language": meta.get("language", ""),
                    "class_name": meta.get("class_name", ""),
                    "function_name": meta.get("function_name", ""),
                    "chunk_type": meta.get("chunk_type", ""),
                    "start_line": meta.get("start_line", 0),
                    "end_line": meta.get("end_line", 0),
                }

        ranked = sorted(file_best.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:top_k]

    def hybrid_get_similar_files(
        self,
        query_text: str,
        top_k: int = 20,
        repo_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
        bm25_weight: float = 0.5,
    ) -> list[dict]:
        """
        Hybrid BM25 + semantic file search using Reciprocal Rank Fusion (RRF).

        RRF formula:  score(d) = Σ  1 / (k + rank_i(d))   with k=60.
        Falls back to pure semantic when BM25 index is unavailable.
        """
        if not self._ready:
            return []

        # Semantic ranking
        semantic_hits = self.get_similar_files(
            query_text,
            top_k=top_k * 2,
            repo_filter=repo_filter,
            language_filter=language_filter,
        )
        semantic_ranked = [h["file_path"] for h in semantic_hits]
        semantic_scores = {h["file_path"]: h for h in semantic_hits}

        # BM25 ranking
        try:
            from rag.bm25_index import get_or_build_index
            bm25_idx = get_or_build_index(
                self.client,
                self.collection_name,
                repo_filter=repo_filter,
                language_filter=language_filter,
            )
        except Exception as exc:
            logger.debug(f"BM25 index unavailable: {exc}")
            bm25_idx = None

        if bm25_idx is None:
            return semantic_hits[:top_k]

        bm25_results = bm25_idx.search(query_text, top_k=top_k * 2)
        bm25_ranked = [fp for fp, _ in bm25_results]
        bm25_score_map = {fp: score for fp, score in bm25_results}

        # RRF fusion
        from utils.ranking import reciprocal_rank_fusion
        fused_with_scores = reciprocal_rank_fusion(
            [semantic_ranked, bm25_ranked],
            weights=[1.0 - bm25_weight, bm25_weight],
        )
        fused = [fp for fp, _ in fused_with_scores]

        fused_score_map = dict(fused_with_scores)
        output = []
        for fp in fused[:top_k]:
            base = semantic_scores.get(fp, {
                "file_path": fp, "score": 0.0,
                "package_name": "", "language": language_filter or "",
                "class_name": "", "function_name": "",
                "chunk_type": "", "start_line": 0, "end_line": 0,
            })
            output.append({
                **base,
                "score": fused_score_map.get(fp, 0.0),
                "bm25_score": bm25_score_map.get(fp, 0.0),
                "semantic_score": base.get("score", 0.0),
            })

        return output

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_filter(
        self,
        repo_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
        chunk_type_filter: Optional[str] = None,
        package_filter: Optional[str] = None,
        file_filter: Optional[str] = None,
    ):
        """Build a Qdrant Filter from the optional keyword filters."""
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
        if chunk_type_filter:
            conditions.append(models.FieldCondition(
                key="chunk_type", match=models.MatchValue(value=chunk_type_filter)
            ))
        if package_filter:
            # Text match — requires a text index on package_name (see indexer notes)
            conditions.append(models.FieldCondition(
                key="package_name", match=models.MatchText(text=package_filter)
            ))
        if file_filter:
            conditions.append(models.FieldCondition(
                key="file_path", match=models.MatchText(text=file_filter)
            ))

        if not conditions:
            return None
        return models.Filter(must=conditions)

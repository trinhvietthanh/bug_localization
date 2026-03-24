"""
Semantic retriever for the RAG pipeline.
Queries the ChromaDB vector store to find relevant code.
"""

import logging
from functools import lru_cache
from typing import Optional

import chromadb

from rag.embedder import CodeEmbedder

logger = logging.getLogger(__name__)


class CodeRetriever:
    """Retrieves code chunks from ChromaDB based on semantic similarity."""

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
        self._embed_cache: dict[str, list[float]] = {}

        # Connect to ChromaDB
        self.client = chromadb.PersistentClient(path=persist_directory)
        try:
            self.collection = self.client.get_collection(collection_name)
        except Exception:
            logger.warning(
                f"Collection '{collection_name}' not found. "
                "Run indexing first."
            )
            self.collection = None

    def query(
        self,
        query_text: str,
        top_k: int = 10,
        file_filter: Optional[str] = None,
        chunk_type_filter: Optional[str] = None,
    ) -> list[tuple[dict, float]]:
        """
        Query the vector store for relevant code.

        Args:
            query_text: Natural language query
            top_k: Number of results to return
            file_filter: Optional file path filter (substring match)
            chunk_type_filter: Optional chunk type filter ("function", "class", "module")

        Returns:
            List of (document_dict, similarity_score) tuples.
            Each document_dict has 'text' and 'metadata' keys.
        """
        if self.collection is None:
            logger.error("No collection available. Run indexing first.")
            return []

        # Build where filters
        where_filter = None
        if file_filter or chunk_type_filter:
            conditions = []
            if file_filter:
                conditions.append({"file_path": {"$contains": file_filter}})
            if chunk_type_filter:
                conditions.append({"chunk_type": chunk_type_filter})

            if len(conditions) == 1:
                where_filter = conditions[0]
            else:
                where_filter = {"$and": conditions}

        # Embed the query (with cache to avoid re-embedding identical queries)
        if query_text in self._embed_cache:
            query_embedding = self._embed_cache[query_text]
        else:
            query_embedding = self.embedder.embed_text(query_text)
            self._embed_cache[query_text] = query_embedding

        # Query ChromaDB
        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return []

        # Parse results
        output = []
        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0]

            for doc, meta, dist in zip(docs, metas, distances):
                # ChromaDB returns cosine distance; convert to similarity
                similarity = 1.0 - dist
                output.append((
                    {"text": doc, "metadata": meta},
                    similarity,
                ))

        return output

    def query_formatted(
        self,
        query_text: str,
        top_k: int = 10,
    ) -> str:
        """Get formatted query results as a string."""
        results = self.query(query_text, top_k)

        if not results:
            return "No results found."

        lines = [f"=== Semantic Search Results for: '{query_text}' ===\n"]
        for i, (doc, score) in enumerate(results, 1):
            meta = doc["metadata"]
            location = meta.get("file_path", "?")
            if meta.get("class_name"):
                location += f"::{meta['class_name']}"
            if meta.get("function_name"):
                location += f".{meta['function_name']}"

            lines.append(
                f"{i}. [{score:.3f}] {location} "
                f"(L{meta.get('start_line', '?')}-{meta.get('end_line', '?')})"
            )
            # Show truncated code snippet
            text = doc["text"][:150].replace("\n", " ")
            lines.append(f"   {text}...")
            lines.append("")

        return "\n".join(lines)

    def get_similar_files(
        self,
        query_text: str,
        top_k: int = 20,
    ) -> list[tuple[str, float]]:
        """
        Get unique files ranked by their best chunk similarity.

        Returns:
            List of (file_path, best_similarity_score) tuples
        """
        # Fetch enough chunks to cover top_k unique files, but cap the multiplier
        results = self.query(query_text, top_k=min(top_k * 2, top_k + 30))

        file_scores = {}
        for doc, score in results:
            fp = doc["metadata"].get("file_path", "")
            if fp and (fp not in file_scores or score > file_scores[fp]):
                file_scores[fp] = score

        ranked = sorted(file_scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]

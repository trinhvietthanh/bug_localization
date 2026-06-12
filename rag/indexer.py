"""
Codebase indexer for the RAG pipeline.
Indexes all code files into a Qdrant vector store.
"""

import os
import logging
import uuid
from pathlib import Path
from typing import Optional

from rag.embedder import CodeEmbedder, CodeChunk

logger = logging.getLogger(__name__)

# File extensions to index
INDEXABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h",
    ".go", ".rs", ".rb", ".scala", ".kt",
}

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".tox", ".eggs",
    "build", "dist", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "*.egg-info", "test", "tests", "testing",
}


def _is_test_file(filepath: Path) -> bool:
    parts = filepath.parts
    if any(p.lower() in ("test", "tests", "testing") for p in parts):
        return True
    name = filepath.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("test.java")
        or name.endswith("tests.java")
    )


def _chunk_id(repo_id: str, file_path: str, start_line: int, idx: int) -> str:
    """Deterministic UUID5 so re-indexing the same file overwrites old points."""
    key = f"{repo_id}::{file_path}::{start_line}::{idx}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class CodebaseIndexer:
    """Indexes a codebase into Qdrant for semantic search."""

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

        os.makedirs(persist_directory, exist_ok=True)
        from qdrant_client import QdrantClient
        self.client = QdrantClient(path=persist_directory)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_repository(
        self,
        repo_path: str,
        repo_id: Optional[str] = None,
        extensions: Optional[set] = None,
        batch_size: int = 64,
    ) -> int:
        """
        Index all code files in a repository.

        Args:
            repo_path:  Path to the buggy checkout
            repo_id:    Logical identifier stored in payload (e.g. "Chart").
                        Use the *project name*, not the instance id, so all
                        bugs in a project share one index partition.
            extensions: File extensions to include (default: INDEXABLE_EXTENSIONS)
            batch_size: Chunks per embedding API call

        Returns:
            Number of chunks indexed
        """
        if extensions is None:
            extensions = INDEXABLE_EXTENSIONS

        repo = Path(repo_path)
        repo_id = repo_id or repo.name

        self._ensure_collection()

        code_files = self._find_code_files(repo, extensions)
        logger.info(f"Found {len(code_files)} code files in {repo_path}")

        all_chunks: list[CodeChunk] = []
        for i, filepath in enumerate(code_files):
            rel_path = str(filepath.relative_to(repo))
            all_chunks.extend(self.embedder.chunk_file(rel_path, repo_path))
            if (i + 1) % 50 == 0:
                logger.info(f"Chunked {i+1}/{len(code_files)} files ({len(all_chunks)} chunks so far)")

        if not all_chunks:
            logger.warning("No chunks produced — nothing indexed")
            return 0

        logger.info(f"Total chunks to embed: {len(all_chunks)}")

        from qdrant_client import models

        total = 0
        num_batches = (len(all_chunks) + batch_size - 1) // batch_size
        for batch_idx in range(num_batches):
            batch = all_chunks[batch_idx * batch_size : (batch_idx + 1) * batch_size]
            texts = [c.text for c in batch]

            embeddings = self.embedder.embed_texts(texts)
            if not embeddings:
                logger.warning(f"Batch {batch_idx+1}/{num_batches}: embed_texts returned empty")
                continue

            # Ensure collection vector size matches the actual model output
            self._ensure_vector_size(len(embeddings[0]))

            points = [
                models.PointStruct(
                    id=_chunk_id(repo_id, c.file_path, c.start_line, batch_idx * batch_size + j),
                    vector=emb,
                    payload={
                        "text": c.text,
                        "file_path": c.file_path,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "function_name": c.function_name,
                        "class_name": c.class_name,
                        "chunk_type": c.chunk_type,
                        "package_name": c.package_name,
                        "language": c.language,
                        "repo_id": repo_id,
                    },
                )
                for j, (c, emb) in enumerate(zip(batch, embeddings))
            ]

            self.client.upsert(collection_name=self.collection_name, points=points)
            total += len(points)
            logger.info(f"Indexed batch {batch_idx+1}/{num_batches} ({total} chunks total)")

        logger.info(f"Indexing complete: {total} chunks indexed for repo_id='{repo_id}'")
        return total

    def clear_index(self):
        """Drop and recreate the collection."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._collection_ready = False
        self._ensure_collection()
        logger.info("Index cleared")

    def delete_repo(self, repo_id: str) -> int:
        """Delete all chunks for a given repo_id. Returns number of deleted points."""
        from qdrant_client import models
        result = self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key="repo_id", match=models.MatchValue(value=repo_id)
                    )]
                )
            ),
        )
        logger.info(f"Deleted chunks for repo_id='{repo_id}'")
        return getattr(result, "deleted_count", 0)

    def get_stats(self) -> dict:
        """Return collection statistics."""
        try:
            info = self.client.get_collection(self.collection_name)
            return {
                "collection": self.collection_name,
                "total_chunks": info.points_count,
                "vector_size": info.config.params.vectors.size
                if hasattr(info.config.params.vectors, "size") else "?",
            }
        except Exception as e:
            return {"collection": self.collection_name, "total_chunks": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_collection(self):
        """Create collection if it does not exist yet (vector size TBD)."""
        try:
            self.client.get_collection(self.collection_name)
            self._collection_ready = True
        except Exception:
            self._collection_ready = False
            # Will be created with correct size on first embed batch
            logger.info(f"Collection '{self.collection_name}' does not exist yet; will create on first upsert")

    def _ensure_vector_size(self, size: int):
        """Lazily create collection once we know the embedding dimension."""
        if getattr(self, "_collection_ready", False):
            return
        from qdrant_client import models
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=size,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info(f"Created collection '{self.collection_name}' with size={size}")
        self._collection_ready = True

    def _find_code_files(self, repo: Path, extensions: set) -> list[Path]:
        """Recursively find indexable source files, skipping test/build dirs."""
        files = []
        for root, dirs, filenames in os.walk(repo):
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIRS and "test" not in d.lower()
            ]
            for filename in filenames:
                filepath = Path(root) / filename
                if _is_test_file(filepath):
                    continue
                if filepath.suffix in extensions:
                    try:
                        if filepath.stat().st_size <= 1_000_000:
                            files.append(filepath)
                    except OSError:
                        continue
        return sorted(files)

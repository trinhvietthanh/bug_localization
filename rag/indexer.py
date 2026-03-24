"""
Codebase indexer for the RAG pipeline.
Indexes all code files into a ChromaDB vector store.
"""

import os
import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings

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
    ".pytest_cache", "*.egg-info", "test", "tests", "testing"
}

def _is_test_file(filepath: Path) -> bool:
    """Check if file is a test file based on path."""
    parts = filepath.parts
    if "test" in parts or "tests" in parts or "testing" in parts:
        return True
    name = filepath.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or name.endswith("test.java") or name.endswith("tests.java")


class CodebaseIndexer:
    """Indexes a codebase into ChromaDB for semantic search."""

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

        # Initialize ChromaDB
        os.makedirs(persist_directory, exist_ok=True)
        self.client = chromadb.PersistentClient(path=persist_directory)

    def index_repository(
        self,
        repo_path: str,
        extensions: set[str] = None,
        batch_size: int = 100,
    ) -> int:
        """
        Index all code files in a repository.

        Args:
            repo_path: Path to the repository to index
            extensions: File extensions to include
            batch_size: Number of chunks to embed at once

        Returns:
            Number of chunks indexed
        """
        if extensions is None:
            extensions = INDEXABLE_EXTENSIONS

        # Create or get collection
        collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        repo = Path(repo_path)
        all_chunks = []

        # Collect all files
        code_files = self._find_code_files(repo, extensions)
        logger.info(f"Found {len(code_files)} code files to index")

        # Chunk all files
        for i, filepath in enumerate(code_files):
            rel_path = str(filepath.relative_to(repo))
            chunks = self.embedder.chunk_file(rel_path, repo_path)
            all_chunks.extend(chunks)

            if (i + 1) % 50 == 0:
                logger.info(f"Chunked {i + 1}/{len(code_files)} files ({len(all_chunks)} chunks)")

        logger.info(f"Total chunks: {len(all_chunks)}")

        if not all_chunks:
            return 0

        # Batch embed and upsert
        for batch_start in range(0, len(all_chunks), batch_size):
            batch = all_chunks[batch_start:batch_start + batch_size]
            texts = [c.text for c in batch]

            # Generate embeddings
            embeddings = self.embedder.embed_texts(texts)

            # Prepare data for ChromaDB
            ids = [
                f"{c.file_path}::{c.start_line}-{c.end_line}::{batch_start + j}"
                for j, c in enumerate(batch)
            ]
            metadatas = [
                {
                    "file_path": c.file_path,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "function_name": c.function_name,
                    "class_name": c.class_name,
                    "chunk_type": c.chunk_type,
                }
                for c in batch
            ]

            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            logger.info(
                f"Indexed batch {batch_start // batch_size + 1}/"
                f"{(len(all_chunks) + batch_size - 1) // batch_size}"
            )

        logger.info(f"Indexing complete: {len(all_chunks)} chunks indexed")
        return len(all_chunks)

    def _find_code_files(self, repo: Path, extensions: set[str]) -> list[Path]:
        """Find all code files in the repository."""
        files = []
        for root, dirs, filenames in os.walk(repo):
            # Skip unwanted directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and "test" not in d.lower()]

            for filename in filenames:
                filepath = Path(root) / filename
                
                if _is_test_file(filepath):
                    continue
                    
                if filepath.suffix in extensions:
                    # Skip very large files (>1MB)
                    try:
                        if filepath.stat().st_size <= 1_000_000:
                            files.append(filepath)
                    except OSError:
                        continue

        return sorted(files)

    def clear_index(self):
        """Delete the collection and recreate it."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Index cleared")

    def get_stats(self) -> dict:
        """Get statistics about the index."""
        try:
            collection = self.client.get_collection(self.collection_name)
            return {
                "collection": self.collection_name,
                "total_chunks": collection.count(),
            }
        except Exception:
            return {"collection": self.collection_name, "total_chunks": 0}

"""
Code embedder for the RAG pipeline.
Chunks and embeds source code files using sentence-transformers.
"""

import logging
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """A chunk of code with metadata."""
    text: str
    file_path: str
    start_line: int
    end_line: int
    function_name: str = ""
    class_name: str = ""
    chunk_type: str = "code"  # "function", "class", "module"
    embedding: list = field(default_factory=list)


class CodeEmbedder:
    """Embeds code chunks using sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        """Lazy-load the embedding model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_texts(self, texts: list[str], show_progress: bool = True) -> list[list[float]]:
        """Embed a list of text strings."""
        embeddings = self.model.encode(texts, show_progress_bar=show_progress)
        return embeddings.tolist()

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        embedding = self.model.encode([text], show_progress_bar=False)
        return embedding[0].tolist()

    def chunk_file(
        self,
        file_path: str,
        repo_path: str,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> list[CodeChunk]:
        """
        Chunk a Python file into semantic units (functions/classes).
        Falls back to line-based chunking for non-Python files.

        Args:
            file_path: Relative path to the file
            repo_path: Repository root
            chunk_size: Max characters per chunk (for line-based chunking)
            chunk_overlap: Overlap between chunks

        Returns:
            List of CodeChunk objects
        """
        full_path = Path(repo_path) / file_path

        if not full_path.exists() or not full_path.is_file():
            return []

        try:
            content = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        if not content.strip():
            return []

        # Use AST-based chunking for Python files
        if file_path.endswith(".py"):
            chunks = self._chunk_python_ast(content, file_path)
            if chunks:  # If AST parsing succeeded
                return chunks

        # Fallback: line-based chunking
        return self._chunk_by_lines(content, file_path, chunk_size, chunk_overlap)

    def _chunk_python_ast(self, content: str, file_path: str) -> list[CodeChunk]:
        """Chunk a Python file using AST to split by function/class boundaries."""
        import ast

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        lines = content.split("\n")
        chunks = []

        # Module-level docstring / header
        # Collect info about top-level imports and assignments
        header_end = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr)):
                header_end = max(header_end, node.end_lineno or node.lineno)
            else:
                break

        if header_end > 0:
            header_text = "\n".join(lines[:header_end])
            if header_text.strip():
                chunks.append(CodeChunk(
                    text=header_text,
                    file_path=file_path,
                    start_line=1,
                    end_line=header_end,
                    chunk_type="module",
                ))

        # Extract functions and classes
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_text = "\n".join(lines[node.lineno - 1:node.end_lineno])
                chunks.append(CodeChunk(
                    text=class_text,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                    class_name=node.name,
                    chunk_type="class",
                ))

                # Also add individual methods as separate chunks
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_text = "\n".join(lines[child.lineno - 1:child.end_lineno])
                        chunks.append(CodeChunk(
                            text=method_text,
                            file_path=file_path,
                            start_line=child.lineno,
                            end_line=child.end_lineno,
                            function_name=child.name,
                            class_name=node.name,
                            chunk_type="function",
                        ))

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_text = "\n".join(lines[node.lineno - 1:node.end_lineno])
                chunks.append(CodeChunk(
                    text=func_text,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                    function_name=node.name,
                    chunk_type="function",
                ))

        return chunks

    def _chunk_by_lines(
        self,
        content: str,
        file_path: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[CodeChunk]:
        """Fallback line-based chunking."""
        lines = content.split("\n")
        chunks = []

        # Approximate lines per chunk
        avg_line_len = max(1, len(content) / max(1, len(lines)))
        lines_per_chunk = max(10, int(chunk_size / avg_line_len))
        overlap_lines = max(2, int(chunk_overlap / avg_line_len))

        start = 0
        while start < len(lines):
            end = min(start + lines_per_chunk, len(lines))
            chunk_text = "\n".join(lines[start:end])

            if chunk_text.strip():
                chunks.append(CodeChunk(
                    text=chunk_text,
                    file_path=file_path,
                    start_line=start + 1,
                    end_line=end,
                    chunk_type="code",
                ))

            start = end - overlap_lines
            if start >= len(lines) - overlap_lines:
                break

        return chunks

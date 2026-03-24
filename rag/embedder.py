"""
Code embedder for the RAG pipeline.
Chunks and embeds source code files using either a local sentence-transformers
model or an OpenAI-compatible remote embedding API.
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
    """
    Embeds code chunks.

    Backends (auto-selected based on constructor args):
    - **API mode**: when ``api_base`` and ``api_key`` are provided, calls an
      OpenAI-compatible ``/embeddings`` endpoint (e.g. Qwen3-Embedding on
      ai-gateway.vinbase.ai).
    - **Local mode** (default): loads a sentence-transformers model locally.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        api_base: str = "",
        api_key: str = "",
        batch_size: int = 64,
    ):
        self.model_name = model_name
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key or ""
        self.batch_size = batch_size
        self._model = None          # local sentence-transformers model
        self._api_client = None     # openai.OpenAI client for remote API

    # ------------------------------------------------------------------
    # Backend selection
    # ------------------------------------------------------------------

    @property
    def _use_api(self) -> bool:
        return bool(self.api_base and self.api_key)

    @property
    def local_model(self):
        """Lazy-load local sentence-transformers model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading local embedding model: {self.model_name}")
            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    trust_remote_code=True,
                )
            except TypeError:
                self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def api_client(self):
        """Lazy-init OpenAI client pointed at custom embedding endpoint."""
        if self._api_client is None:
            from openai import OpenAI
            # Strip trailing /embeddings if user pasted the full URL
            base = self.api_base
            if base.endswith("/embeddings"):
                base = base[: -len("/embeddings")]
            logger.info(
                f"Using remote embedding API: {base} model={self.model_name}"
            )
            self._api_client = OpenAI(api_key=self.api_key, base_url=base)
        return self._api_client

    # ------------------------------------------------------------------
    # Public embed API
    # ------------------------------------------------------------------

    def embed_texts(
        self, texts: list[str], show_progress: bool = True
    ) -> list[list[float]]:
        """Embed a list of text strings, returns list of embedding vectors."""
        if self._use_api:
            return self._embed_texts_api(texts, show_progress=show_progress)
        return self._embed_texts_local(texts, show_progress=show_progress)

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        return self.embed_texts([text], show_progress=False)[0]

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _embed_texts_local(
        self, texts: list[str], show_progress: bool = True
    ) -> list[list[float]]:
        embeddings = self.local_model.encode(
            texts, show_progress_bar=show_progress, batch_size=self.batch_size
        )
        return embeddings.tolist()

    def _embed_texts_api(
        self, texts: list[str], show_progress: bool = True
    ) -> list[list[float]]:
        """Call OpenAI-compatible /embeddings endpoint in batches."""
        from tqdm import tqdm

        results: list[list[float]] = []
        batches = [
            texts[i : i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]
        iterator = tqdm(batches, desc="Embedding (API)") if show_progress else batches
        for batch in iterator:
            try:
                response = self.api_client.embeddings.create(
                    model=self.model_name,
                    input=batch,
                )
                batch_embeddings = [item.embedding for item in response.data]
                results.extend(batch_embeddings)
            except Exception as e:
                logger.error(f"Embedding API call failed: {e}")
                # Fall back to zero vectors to avoid crashing the pipeline
                dim = len(results[0]) if results else 768
                results.extend([[0.0] * dim for _ in batch])
        return results

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def chunk_file(
        self,
        file_path: str,
        repo_path: str,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> list[CodeChunk]:
        """
        Chunk a source file into semantic units (functions/classes).
        Falls back to line-based chunking for non-Python files.
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

        if file_path.endswith(".py"):
            chunks = self._chunk_python_ast(content, file_path)
            if chunks:
                return chunks

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

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                chunks.append(CodeChunk(
                    text=class_text,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                    class_name=node.name,
                    chunk_type="class",
                ))
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_text = "\n".join(
                            lines[child.lineno - 1 : child.end_lineno]
                        )
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
                func_text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
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

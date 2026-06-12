"""
Code embedder for the RAG pipeline.
Chunks and embeds source code files using either a local sentence-transformers
model or an OpenAI-compatible remote embedding API.

Enhanced indexing for Ground-truth File Identification (GFI):
- Java AST-based chunking via javalang (method/class level)
- File-level summary chunks (signatures without bodies)
- Enriched chunk text with structural context prefix
- Package name extraction for Java files
"""

import logging
import re
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_JAVA_MODIFIER_PAT = re.compile(
    r"^\s*(public|protected|private|static|final|abstract|synchronized|native|"
    r"strictfp|transient|volatile|\s)+\s+\w"
)


@dataclass
class CodeChunk:
    """A chunk of code with metadata."""

    text: str
    file_path: str
    start_line: int
    end_line: int
    function_name: str = ""
    class_name: str = ""
    chunk_type: str = "code"  # "function", "class", "module", "file_summary"
    package_name: str = ""
    language: str = ""
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
        self._model = None
        self._api_client = None

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

            base = self.api_base
            if base.endswith("/embeddings"):
                base = base[: -len("/embeddings")]
            logger.info(f"Using remote embedding API: {base} model={self.model_name}")
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
        Tries language-specific AST chunking first, falls back to line-based.
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

        if file_path.endswith(".java"):
            chunks = self._chunk_java_ast(content, file_path)
            if chunks:
                return chunks

        return self._chunk_by_lines(content, file_path, chunk_size, chunk_overlap)

    # ------------------------------------------------------------------
    # Python AST chunking
    # ------------------------------------------------------------------

    def _chunk_python_ast(self, content: str, file_path: str) -> list[CodeChunk]:
        """Chunk a Python file using AST to split by function/class boundaries."""
        import ast

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        lines = content.split("\n")
        chunks = []
        lang = "python"

        header_end = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr)):
                header_end = max(header_end, node.end_lineno or node.lineno)
            else:
                break

        # File summary
        summary_lines = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                summary_lines.append(f"class {node.name}:")
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = self._python_args_str(child.args)
                        summary_lines.append(f"    def {child.name}({args}): ...")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = self._python_args_str(node.args)
                summary_lines.append(f"def {node.name}({args}): ...")

        if summary_lines:
            prefix = f"FILE: {file_path} | LANGUAGE: python"
            chunks.append(
                CodeChunk(
                    text=prefix + "\n" + "\n".join(summary_lines),
                    file_path=file_path,
                    start_line=1,
                    end_line=1,
                    chunk_type="file_summary",
                    language=lang,
                )
            )

        if header_end > 0:
            header_text = "\n".join(lines[:header_end])
            if header_text.strip():
                prefix = f"FILE: {file_path} | LANGUAGE: python | SECTION: imports\n"
                chunks.append(
                    CodeChunk(
                        text=prefix + header_text,
                        file_path=file_path,
                        start_line=1,
                        end_line=header_end,
                        chunk_type="module",
                        language=lang,
                    )
                )

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                prefix = f"FILE: {file_path} | LANGUAGE: python | CLASS: {node.name}\n"
                chunks.append(
                    CodeChunk(
                        text=prefix + class_text,
                        file_path=file_path,
                        start_line=node.lineno,
                        end_line=node.end_lineno,
                        class_name=node.name,
                        chunk_type="class",
                        language=lang,
                    )
                )
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_text = "\n".join(
                            lines[child.lineno - 1 : child.end_lineno]
                        )
                        prefix = (
                            f"FILE: {file_path} | LANGUAGE: python"
                            f" | CLASS: {node.name} | METHOD: {child.name}\n"
                        )
                        chunks.append(
                            CodeChunk(
                                text=prefix + method_text,
                                file_path=file_path,
                                start_line=child.lineno,
                                end_line=child.end_lineno,
                                function_name=child.name,
                                class_name=node.name,
                                chunk_type="function",
                                language=lang,
                            )
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                prefix = (
                    f"FILE: {file_path} | LANGUAGE: python"
                    f" | FUNCTION: {node.name}\n"
                )
                chunks.append(
                    CodeChunk(
                        text=prefix + func_text,
                        file_path=file_path,
                        start_line=node.lineno,
                        end_line=node.end_lineno,
                        function_name=node.name,
                        chunk_type="function",
                        language=lang,
                    )
                )

        return chunks

    def _python_args_str(self, args) -> str:
        """Render Python function arguments as a compact string."""
        parts = [a.arg for a in args.args]
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Java AST chunking
    # ------------------------------------------------------------------

    def _chunk_java_ast(self, content: str, file_path: str) -> list[CodeChunk]:
        """
        Chunk a Java file using javalang AST to extract:
        - A file-level summary chunk (package + class/method signatures)
        - Per-class chunks
        - Per-method chunks

        Each chunk text is prefixed with structural context to improve GFI.
        """
        try:
            import javalang  # type: ignore

            tree = javalang.parse.parse(content)
        except Exception:
            return []

        lines = content.split("\n")
        chunks: list[CodeChunk] = []
        lang = "java"

        package_name = tree.package.name if tree.package else ""

        # ---- collect all type declarations with positions ----
        type_decls: list[tuple] = []
        for path, node in tree.filter(javalang.tree.TypeDeclaration):
            if hasattr(node, "position") and node.position is not None:
                type_decls.append((path, node))

        # ---- build file summary ----
        summary_lines: list[str] = []
        if package_name:
            summary_lines.append(f"package {package_name};")

        for _, cls in type_decls:
            cls_kind = type(cls).__name__.replace("Declaration", "")
            summary_lines.append(f"\n{cls_kind}: {cls.name}")
            if hasattr(cls, "methods"):
                for method in cls.methods or []:
                    ret = self._java_return_type(method)
                    params = self._java_params_str(method)
                    summary_lines.append(f"  {ret} {method.name}({params})")
            if hasattr(cls, "constructors"):
                for ctor in cls.constructors or []:
                    params = self._java_params_str(ctor)
                    summary_lines.append(f"  {cls.name}({params})  [constructor]")

        if summary_lines:
            prefix = f"FILE: {file_path} | LANGUAGE: java"
            if package_name:
                prefix += f" | PACKAGE: {package_name}"
            chunks.append(
                CodeChunk(
                    text=prefix + "\n" + "\n".join(summary_lines),
                    file_path=file_path,
                    start_line=1,
                    end_line=1,
                    chunk_type="file_summary",
                    package_name=package_name,
                    language=lang,
                )
            )

        # ---- per-class and per-method chunks ----
        for _, cls in type_decls:
            class_start = cls.position.line
            class_name = cls.name

            # Collect all method start lines for this class to bound class chunk
            method_starts = []
            for method in (cls.methods or []) + (cls.constructors or []):
                if hasattr(method, "position") and method.position is not None:
                    method_starts.append(method.position.line)

            class_end = self._find_block_end(lines, class_start - 1)

            # Class header chunk (lines up to first method, capped at 30 lines)
            first_method_line = min(method_starts) if method_starts else class_end
            class_header_end = min(first_method_line - 1, class_start + 29, class_end)
            class_header_end = max(class_header_end, class_start)
            class_header_text = "\n".join(lines[class_start - 1 : class_header_end])

            prefix = (
                f"FILE: {file_path} | LANGUAGE: java"
                + (f" | PACKAGE: {package_name}" if package_name else "")
                + f" | CLASS: {class_name}\n"
            )
            chunks.append(
                CodeChunk(
                    text=prefix + class_header_text,
                    file_path=file_path,
                    start_line=class_start,
                    end_line=class_header_end,
                    class_name=class_name,
                    chunk_type="class",
                    package_name=package_name,
                    language=lang,
                )
            )

            # Method chunks
            all_callables = list(cls.methods or []) + list(cls.constructors or [])
            all_callables = [
                m
                for m in all_callables
                if hasattr(m, "position") and m.position is not None
            ]
            all_callables.sort(key=lambda m: m.position.line)

            for method in all_callables:
                method_start = method.position.line
                method_end = self._find_block_end(lines, method_start - 1)
                method_text = "\n".join(lines[method_start - 1 : method_end])

                method_name = method.name
                ret = self._java_return_type(method)
                params = self._java_params_str(method)

                prefix = (
                    f"FILE: {file_path} | LANGUAGE: java"
                    + (f" | PACKAGE: {package_name}" if package_name else "")
                    + f" | CLASS: {class_name}"
                    + f" | METHOD: {method_name}({params})"
                    + (f" -> {ret}" if ret else "")
                    + "\n"
                )
                chunks.append(
                    CodeChunk(
                        text=prefix + method_text,
                        file_path=file_path,
                        start_line=method_start,
                        end_line=method_end,
                        function_name=method_name,
                        class_name=class_name,
                        chunk_type="function",
                        package_name=package_name,
                        language=lang,
                    )
                )

        return chunks

    def _find_block_end(self, lines: list[str], start_idx: int) -> int:
        """
        Find the closing line of a brace-delimited block (Java/JS/etc).
        Starts scanning from start_idx (0-based). Returns 1-based end line.
        Handles single-line strings and ignores braces in comments.
        """
        depth = 0
        found_open = False
        in_string = False
        in_char = False
        in_line_comment = False
        in_block_comment = False

        for i, line in enumerate(lines[start_idx:], start_idx):
            j = 0
            while j < len(line):
                ch = line[j]
                next_ch = line[j + 1] if j + 1 < len(line) else ""

                if in_block_comment:
                    if ch == "*" and next_ch == "/":
                        in_block_comment = False
                        j += 2
                        continue
                    j += 1
                    continue

                if in_line_comment:
                    break

                if in_string:
                    if ch == "\\" :
                        j += 2
                        continue
                    if ch == '"':
                        in_string = False
                    j += 1
                    continue

                if in_char:
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "'":
                        in_char = False
                    j += 1
                    continue

                if ch == "/" and next_ch == "/":
                    in_line_comment = True
                    break
                if ch == "/" and next_ch == "*":
                    in_block_comment = True
                    j += 2
                    continue
                if ch == '"':
                    in_string = True
                    j += 1
                    continue
                if ch == "'":
                    in_char = True
                    j += 1
                    continue

                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
                    depth -= 1

                j += 1

            in_line_comment = False

            if found_open and depth == 0:
                return i + 1

        return len(lines)

    def _java_return_type(self, method) -> str:
        """Extract return type string from a javalang method node."""
        try:
            rt = method.return_type
            if rt is None:
                return "void"
            if hasattr(rt, "name"):
                return rt.name
        except Exception:
            pass
        return ""

    def _java_params_str(self, method) -> str:
        """Render Java method parameters as a compact type-only string."""
        try:
            params = method.parameters or []
            parts = []
            for p in params:
                type_name = p.type.name if hasattr(p.type, "name") else str(p.type)
                parts.append(type_name)
            return ", ".join(parts)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Line-based fallback chunking
    # ------------------------------------------------------------------

    def _chunk_by_lines(
        self,
        content: str,
        file_path: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[CodeChunk]:
        """Fallback line-based chunking for unsupported languages."""
        lines = content.split("\n")
        chunks = []

        ext = Path(file_path).suffix.lstrip(".")
        lang_map = {
            "js": "javascript", "ts": "typescript", "go": "go",
            "rs": "rust", "rb": "ruby", "scala": "scala",
            "kt": "kotlin", "c": "c", "cpp": "cpp", "h": "c",
        }
        lang = lang_map.get(ext, ext)

        avg_line_len = max(1, len(content) / max(1, len(lines)))
        lines_per_chunk = max(10, int(chunk_size / avg_line_len))
        overlap_lines = max(2, int(chunk_overlap / avg_line_len))

        start = 0
        while start < len(lines):
            end = min(start + lines_per_chunk, len(lines))
            chunk_text = "\n".join(lines[start:end])

            if chunk_text.strip():
                prefix = f"FILE: {file_path} | LANGUAGE: {lang} | LINES: {start+1}-{end}\n"
                chunks.append(
                    CodeChunk(
                        text=prefix + chunk_text,
                        file_path=file_path,
                        start_line=start + 1,
                        end_line=end,
                        chunk_type="code",
                        language=lang,
                    )
                )

            start = end - overlap_lines
            if start >= len(lines) - overlap_lines:
                break

        return chunks

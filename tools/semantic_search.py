"""
Semantic search tool for agents.
Interface to the RAG pipeline for code similarity search.

Enhanced for GFI: exposes package_filter and language_filter,
and adds a file-level summary search for fast structural lookup.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SemanticResult:
    """A semantic search result."""

    file_path: str
    chunk_text: str
    function_name: str
    class_name: str
    start_line: int
    end_line: int
    similarity_score: float
    package_name: str = ""
    language: str = ""
    chunk_type: str = ""

    def to_str(self) -> str:
        location = self.file_path
        if self.package_name:
            location = f"{self.package_name} > {location}"
        if self.class_name:
            location += f"::{self.class_name}"
        if self.function_name:
            location += f".{self.function_name}"
        tag = f" [{self.chunk_type}]" if self.chunk_type else ""
        return (
            f"[Score: {self.similarity_score:.3f}]{tag} {location} "
            f"(L{self.start_line}-{self.end_line})\n"
            f"  {self.chunk_text[:200]}..."
        )


def semantic_search(
    query: str,
    retriever,
    top_k: int = 10,
    repo_filter: Optional[str] = None,
    package_filter: Optional[str] = None,
    language_filter: Optional[str] = None,
    chunk_type_filter: Optional[str] = None,
) -> list[SemanticResult]:
    """
    Search the codebase semantically using the RAG retriever.

    Args:
        query: Natural language description of what to search for
        retriever: Initialized RAG retriever instance
        top_k: Number of results to return
        repo_filter: Restrict to a specific repository id
        package_filter: Restrict to Java classes within a package (substring)
        language_filter: Restrict to a language ("java", "python", …)
        chunk_type_filter: Restrict to a chunk type ("function", "class",
            "file_summary", "module", "code")

    Returns:
        List of SemanticResult objects ranked by similarity
    """
    if retriever is None:
        logger.warning("No retriever available; semantic search disabled")
        return []

    raw_results = retriever.query(
        query,
        top_k=top_k,
        repo_filter=repo_filter,
        package_filter=package_filter,
        language_filter=language_filter,
        chunk_type_filter=chunk_type_filter,
    )

    results = []
    for doc, score in raw_results:
        metadata = doc.get("metadata", {})
        results.append(
            SemanticResult(
                file_path=metadata.get("file_path", ""),
                chunk_text=doc.get("text", ""),
                function_name=metadata.get("function_name", ""),
                class_name=metadata.get("class_name", ""),
                start_line=metadata.get("start_line", 0),
                end_line=metadata.get("end_line", 0),
                similarity_score=score,
                package_name=metadata.get("package_name", ""),
                language=metadata.get("language", ""),
                chunk_type=metadata.get("chunk_type", ""),
            )
        )

    return results


def semantic_search_formatted(
    query: str,
    retriever,
    top_k: int = 10,
    repo_filter: Optional[str] = None,
    package_filter: Optional[str] = None,
    language_filter: Optional[str] = None,
) -> str:
    """Get formatted semantic search results as a string."""
    results = semantic_search(
        query,
        retriever,
        top_k,
        repo_filter=repo_filter,
        package_filter=package_filter,
        language_filter=language_filter,
    )

    if not results:
        return "No semantic search results found."

    lines = [f"Semantic search for: '{query}'\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.to_str()}")
        lines.append("")

    return "\n".join(lines)


def semantic_file_search_formatted(
    query: str,
    retriever,
    top_k: int = 10,
    repo_filter: Optional[str] = None,
    language_filter: Optional[str] = None,
) -> str:
    """
    File-level hybrid search (BM25 + semantic RRF) returning one entry per file.

    Uses hybrid_get_similar_files when available (BM25 index built from ChromaDB),
    falling back to pure semantic search when BM25 is unavailable.
    """
    if retriever is None:
        return "No retriever available; semantic search disabled."

    # Prefer hybrid search; fall back to pure semantic
    hybrid_fn = getattr(retriever, "hybrid_get_similar_files", None)
    if hybrid_fn is not None:
        file_hits = hybrid_fn(
            query,
            top_k=top_k,
            repo_filter=repo_filter,
            language_filter=language_filter,
        )
        search_label = "Hybrid (BM25+semantic)"
    else:
        file_hits = retriever.get_similar_files(
            query,
            top_k=top_k,
            repo_filter=repo_filter,
            language_filter=language_filter,
        )
        search_label = "Semantic"

    if not file_hits:
        return "No files found matching the query."

    lines = [f"{search_label} file search for: '{query}'\n"]
    for i, hit in enumerate(file_hits, 1):
        pkg = f" [{hit['package_name']}]" if hit.get("package_name") else ""
        lang = f" ({hit['language']})" if hit.get("language") else ""
        bm25_tag = ""
        if "bm25_score" in hit:
            bm25_tag = f" bm25={hit['bm25_score']:.3f} sem={hit.get('semantic_score', 0):.3f}"
        lines.append(
            f"{i}. [RRF: {hit['score']:.4f}]{bm25_tag}{pkg}{lang} {hit['file_path']}"
        )
    lines.append("")

    return "\n".join(lines)


# Tool descriptions for LLM agents

TOOL_DESCRIPTION = {
    "name": "semantic_search",
    "description": (
        "Search the codebase using natural language queries. "
        "Finds code semantically similar to the query, including classes, methods, "
        "and file summaries. "
        "Use this to find code related to a specific functionality, error type, or "
        "class/method name even if the exact words do not appear in the code."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language description of the code you're looking for. "
                    "E.g., 'function that handles date parsing and timezone conversion'"
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default 10)",
                "default": 10,
            },
            "language_filter": {
                "type": "string",
                "description": (
                    "Optional: restrict to a language. "
                    "One of 'java', 'python', 'javascript', 'go', etc."
                ),
            },
            "package_filter": {
                "type": "string",
                "description": (
                    "Optional: restrict to Java classes within a package "
                    "(substring match, e.g. 'org.apache.commons.lang')"
                ),
            },
            "chunk_type_filter": {
                "type": "string",
                "description": (
                    "Optional: restrict to a chunk type. "
                    "One of 'function', 'class', 'file_summary', 'module', 'code'."
                ),
            },
        },
        "required": ["query"],
    },
}

FILE_SEARCH_TOOL_DESCRIPTION = {
    "name": "semantic_file_search",
    "description": (
        "Search for relevant SOURCE FILES (not individual methods) using natural language. "
        "Returns one ranked entry per file. Use this as a fast first-pass to identify "
        "which files are most likely to contain the bug before diving into methods."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language query describing the buggy behaviour",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of files to return (default 10)",
                "default": 10,
            },
            "language_filter": {
                "type": "string",
                "description": "Optional language filter ('java', 'python', …)",
            },
        },
        "required": ["query"],
    },
}

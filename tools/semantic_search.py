"""
Semantic search tool for agents.
Interface to the RAG pipeline for code similarity search.
"""

import logging
from dataclasses import dataclass

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

    def to_str(self) -> str:
        location = self.file_path
        if self.class_name:
            location += f"::{self.class_name}"
        if self.function_name:
            location += f".{self.function_name}"
        return (
            f"[Score: {self.similarity_score:.3f}] {location} "
            f"(L{self.start_line}-{self.end_line})\n"
            f"  {self.chunk_text[:200]}..."
        )


def semantic_search(
    query: str,
    retriever,
    top_k: int = 10,
) -> list[SemanticResult]:
    """
    Search the codebase semantically using the RAG retriever.

    Args:
        query: Natural language description of what to search for
        retriever: Initialized RAG retriever instance
        top_k: Number of results to return

    Returns:
        List of SemanticResult objects ranked by similarity
    """
    if retriever is None:
        logger.warning("No retriever available; semantic search disabled")
        return []

    raw_results = retriever.query(query, top_k=top_k)

    results = []
    for doc, score in raw_results:
        metadata = doc.get("metadata", {})
        results.append(SemanticResult(
            file_path=metadata.get("file_path", ""),
            chunk_text=doc.get("text", ""),
            function_name=metadata.get("function_name", ""),
            class_name=metadata.get("class_name", ""),
            start_line=metadata.get("start_line", 0),
            end_line=metadata.get("end_line", 0),
            similarity_score=score,
        ))

    return results


def semantic_search_formatted(
    query: str,
    retriever,
    top_k: int = 10,
) -> str:
    """Get formatted semantic search results as a string."""
    results = semantic_search(query, retriever, top_k)

    if not results:
        return "No semantic search results found."

    lines = [f"Semantic search for: '{query}'\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.to_str()}")
        lines.append("")

    return "\n".join(lines)


# Tool description for LLM agents
TOOL_DESCRIPTION = {
    "name": "semantic_search",
    "description": (
        "Search the codebase using natural language queries. "
        "Finds code that is semantically similar to the query description, "
        "even if the exact words don't appear in the code. "
        "Use this to find code related to a specific functionality or behavior."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language description of the code you're looking for. "
                    "E.g., 'function that handles date parsing and timezone conversion'"
                )
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return",
                "default": 10
            }
        },
        "required": ["query"]
    }
}

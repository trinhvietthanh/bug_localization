"""
Graph RAG search tool for LLM agents.
Provides graph-based code exploration as a tool the agent can invoke.
"""

import logging
import threading
from rag.code_graph import build_code_graph, get_or_build_graph
from rag.graph_retriever import GraphRetriever
from config import config

logger = logging.getLogger(__name__)

# Singleton graph cache (avoid rebuilding per query)
_graph_cache: dict[str, GraphRetriever] = {}
_graph_cache_lock = threading.Lock()


def get_graph_retriever(
    repo_path: str,
    language: str = "auto",
    use_neo4j: bool | None = None,
) -> GraphRetriever:
    """Get or create a GraphRetriever for a repository."""
    cache_key = f"{repo_path}|{'neo4j' if (use_neo4j or (use_neo4j is None and config.neo4j.enabled)) else 'mem'}"

    # Fast path: check without lock first
    with _graph_cache_lock:
        if cache_key in _graph_cache:
            return _graph_cache[cache_key]

    # Slow path: build outside lock (expensive operation)
    logger.info(f"Building code graph for: {repo_path}")
    neo4j_enabled = use_neo4j if use_neo4j is not None else config.neo4j.enabled

    retriever = None
    if neo4j_enabled:
        try:
            neo4j_cfg = config.neo4j
            graph = get_or_build_graph(
                repo_path=repo_path,
                language=language,
                use_neo4j=True,
                neo4j_uri=neo4j_cfg.uri,
                neo4j_user=neo4j_cfg.user,
                neo4j_password=neo4j_cfg.password,
                neo4j_database=neo4j_cfg.database,
            )
            retriever = GraphRetriever(graph=graph, repo_path=repo_path)
            retriever._prepare_indexes()
            logger.info("GraphRetriever using Neo4j backend")
        except ConnectionError as e:
            logger.warning(f"Neo4j unavailable, falling back to in-memory: {e}")

    if retriever is None:
        retriever = GraphRetriever(repo_path=repo_path)
        retriever.build_graph(language=language)

    # Write back under lock (double-check: another thread may have beaten us)
    with _graph_cache_lock:
        if cache_key not in _graph_cache:
            _graph_cache[cache_key] = retriever
        return _graph_cache[cache_key]


def graph_search(
    query: str,
    repo_path: str,
    top_k: int = 10,
    language: str = "auto",
) -> str:
    """
    Search the Code Property Graph for related code elements.

    This tool finds:
    - Functions/methods matching the query (anchor nodes)
    - Functions called BY the matches (callees)
    - Functions that CALL the matches (callers)
    - Sibling methods in the same class
    - Inheritance relationships

    Args:
        query: Natural language query describing the bug or feature
        repo_path: Path to the repository
        top_k: Number of results to return
        language: Programming language ("python", "java", "auto")

    Returns:
        Formatted string with ranked results and code snippets
    """
    retriever = get_graph_retriever(repo_path, language=language)
    return retriever.search_formatted(query, top_k=top_k)


def graph_stats(repo_path: str, language: str = "auto") -> str:
    """Get statistics about the code graph."""
    retriever = get_graph_retriever(repo_path, language=language)
    stats = retriever.graph.stats()
    lines = [
        "=== Code Property Graph Statistics ===",
        f"Total Nodes: {stats['total_nodes']}",
        f"Total Edges: {stats['total_edges']}",
        "",
        "Node Types:",
    ]
    for ntype, count in stats["node_types"].items():
        lines.append(f"  {ntype}: {count}")
    lines.append("")
    lines.append("Edge Types:")
    for etype, count in stats["edge_types"].items():
        lines.append(f"  {etype}: {count}")
    return "\n".join(lines)


def find_callers(
    function_name: str, repo_path: str, language: str = "auto"
) -> str:
    """Find all functions that call a given function."""
    retriever = get_graph_retriever(repo_path, language=language)
    nodes = retriever.graph.find_nodes_by_name(function_name)

    if not nodes:
        return f"Function '{function_name}' not found in the code graph."

    lines = [f"=== Callers of '{function_name}' ===\n"]
    for node in nodes:
        callers = retriever.graph.get_callers(node.id)
        lines.append(f"  {node.id}:")
        if callers:
            for caller in callers:
                lines.append(
                    f"    ← {caller.name} ({caller.file_path}:{caller.start_line})"
                )
        else:
            lines.append("    (no callers found)")
        lines.append("")

    return "\n".join(lines)


def find_callees(
    function_name: str, repo_path: str, language: str = "auto"
) -> str:
    """Find all functions called by a given function."""
    retriever = get_graph_retriever(repo_path, language=language)
    nodes = retriever.graph.find_nodes_by_name(function_name)

    if not nodes:
        return f"Function '{function_name}' not found in the code graph."

    lines = [f"=== Callees of '{function_name}' ===\n"]
    for node in nodes:
        callees = retriever.graph.get_callees(node.id)
        lines.append(f"  {node.id}:")
        if callees:
            for callee in callees:
                lines.append(
                    f"    → {callee.name} ({callee.file_path}:{callee.start_line})"
                )
        else:
            lines.append("    (no callees found)")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────── Agent-integrated wrappers ────────────
# These accept graph_retriever (auto-injected from AgentContext)
# so agents don't need to pass repo_path explicitly.

def agent_graph_search(
    query: str,
    graph_retriever,
    top_k: int = 10,
) -> str:
    """Graph RAG search using the pre-built graph from AgentContext."""
    if graph_retriever is None:
        return "Graph RAG not available (graph not built for this repository)."
    return graph_retriever.search_formatted(query, top_k=top_k)


def agent_find_callers(
    function_name: str,
    graph_retriever,
) -> str:
    """Find all callers of a function using the pre-built graph."""
    if graph_retriever is None:
        return "Graph RAG not available (graph not built for this repository)."
    nodes = graph_retriever.graph.find_nodes_by_name(function_name)
    if not nodes:
        return f"Function '{function_name}' not found in the code graph."

    lines = [f"=== Callers of '{function_name}' ===\n"]
    for node in nodes:
        callers = graph_retriever.graph.get_callers(node.id)
        lines.append(f"  {node.id}:")
        if callers:
            for caller in callers:
                lines.append(
                    f"    <- {caller.name} ({caller.file_path}:{caller.start_line})"
                )
        else:
            lines.append("    (no callers found)")
        lines.append("")
    return "\n".join(lines)


def agent_find_callees(
    function_name: str,
    graph_retriever,
) -> str:
    """Find all callees of a function using the pre-built graph."""
    if graph_retriever is None:
        return "Graph RAG not available (graph not built for this repository)."
    nodes = graph_retriever.graph.find_nodes_by_name(function_name)
    if not nodes:
        return f"Function '{function_name}' not found in the code graph."

    lines = [f"=== Callees of '{function_name}' ===\n"]
    for node in nodes:
        callees = graph_retriever.graph.get_callees(node.id)
        lines.append(f"  {node.id}:")
        if callees:
            for callee in callees:
                lines.append(
                    f"    -> {callee.name} ({callee.file_path}:{callee.start_line})"
                )
        else:
            lines.append("    (no callees found)")
        lines.append("")
    return "\n".join(lines)


# Tool descriptions for LLM agents
AGENT_GRAPH_SEARCH_TOOL = {
    "name": "graph_search",
    "description": (
        "Search the Code Property Graph to find structurally related code. "
        "Unlike text search, this finds: functions that CALL a target, "
        "functions CALLED BY a target, sibling methods in the same class, "
        "and inheritance relationships. Use this to understand code dependencies "
        "and trace execution flow related to the bug."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language query describing the code to find "
                    "(e.g., 'hex number parsing', 'payment validation')"
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results (default: 10)",
            },
        },
        "required": ["query"],
    },
}

AGENT_FIND_CALLERS_TOOL = {
    "name": "find_callers",
    "description": (
        "Find all functions/methods that call a given function. "
        "Useful for impact analysis and understanding how a buggy function is used. "
        "Traces the call graph backwards from a suspicious function."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "function_name": {
                "type": "string",
                "description": "Name of the function to find callers for",
            },
        },
        "required": ["function_name"],
    },
}

AGENT_FIND_CALLEES_TOOL = {
    "name": "find_callees",
    "description": (
        "Find all functions/methods called by a given function. "
        "Useful for tracing execution flow from a suspected entry point "
        "and understanding what code a function depends on."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "function_name": {
                "type": "string",
                "description": "Name of the function to find callees for",
            },
        },
        "required": ["function_name"],
    },
}


GRAPH_SEARCH_TOOL = {
    "name": "graph_search",
    "description": (
        "Search the Code Property Graph to find structurally related code. "
        "Unlike text search, this finds: functions that CALL a target, "
        "functions CALLED BY a target, sibling methods in the same class, "
        "and inheritance relationships. Use this to understand code dependencies "
        "and trace execution flow."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language query describing the code to find "
                    "(e.g., 'hex number parsing', 'payment validation')"
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results (default: 10)",
            },
        },
        "required": ["query"],
    },
}

FIND_CALLERS_TOOL = {
    "name": "find_callers",
    "description": (
        "Find all functions/methods that call a given function. "
        "Useful for impact analysis and understanding how a buggy function is used."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "function_name": {
                "type": "string",
                "description": "Name of the function to find callers for",
            },
        },
        "required": ["function_name"],
    },
}

FIND_CALLEES_TOOL = {
    "name": "find_callees",
    "description": (
        "Find all functions/methods called by a given function. "
        "Useful for tracing execution flow from a suspected entry point."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "function_name": {
                "type": "string",
                "description": "Name of the function to find callees for",
            },
        },
        "required": ["function_name"],
    },
}

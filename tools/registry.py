"""
Central tool registry.

All shared tool (callable, schema) pairs live here once.
Agents declare which tools they need by name::

    from tools.registry import TOOL_REGISTRY

    class MyAgent(BaseAgent):
        TOOLS = ["code_search", "read_file", "parse_logs"]

        def __init__(self):
            super().__init__(name="MyAgent")
            for name in self.TOOLS:
                self.register_tool(*TOOL_REGISTRY[name])

Agent-specific tools (e.g. ``search_tests`` in ComprehensionAgent) are NOT
in this registry — keep them in their agent module and register manually.
"""

from tools.ast_parser import get_file_outline, get_function_source
from tools.code_search import code_search
from tools.file_reader import read_file, list_directory
from tools.git_history import git_log_formatted
from tools.graph_search import (
    AGENT_FIND_CALLERS_TOOL,
    AGENT_FIND_CALLEES_TOOL,
    AGENT_GRAPH_SEARCH_TOOL,
    agent_find_callers,
    agent_find_callees,
    agent_graph_search,
)
from tools.log_parser import TOOL_DESCRIPTION as _LOG_TOOL_DESC
from tools.log_parser import parse_logs_tool
from tools.semantic_search import (
    FILE_SEARCH_TOOL_DESCRIPTION,
    semantic_file_search_formatted,
    semantic_search_formatted,
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_CODE_SEARCH_SCHEMA = {
    "name": "code_search",
    "description": (
        "Search for text or regex patterns across code files. "
        "Returns matching lines with file paths and line numbers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term or regex pattern",
            },
            "is_regex": {
                "type": "boolean",
                "description": "Treat query as a regex pattern",
                "default": False,
            },
            "file_pattern": {
                "type": "string",
                "description": "Glob filter for file names, e.g. '*.java'",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "default": 20,
            },
        },
        "required": ["query"],
    },
}

_READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read the contents of a source file with line numbers.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to the file within the repository",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to read (1-indexed, inclusive)",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to read (1-indexed, inclusive)",
            },
        },
        "required": ["file_path"],
    },
}

_LIST_DIRECTORY_SCHEMA = {
    "name": "list_directory",
    "description": "List directory contents as an indented tree.",
    "parameters": {
        "type": "object",
        "properties": {
            "dir_path": {
                "type": "string",
                "description": "Relative path to the directory",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum recursion depth",
                "default": 2,
            },
        },
        "required": ["dir_path"],
    },
}

_SEMANTIC_SEARCH_SCHEMA = {
    "name": "semantic_search",
    "description": (
        "Search code by natural language meaning. "
        "Finds semantically similar methods, classes, and file summaries. "
        "Supports optional language_filter ('java', 'python', …) and "
        "package_filter (Java package substring match)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language query describing what to find",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return",
                "default": 10,
            },
            "language_filter": {
                "type": "string",
                "description": "Restrict results to a language (e.g. 'java')",
            },
            "package_filter": {
                "type": "string",
                "description": "Restrict results to a Java package substring",
            },
        },
        "required": ["query"],
    },
}

_GIT_LOG_SCHEMA = {
    "name": "git_log",
    "description": (
        "View recent git commit history, optionally filtered to a single file. "
        "Useful for understanding what changed recently in a suspicious file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative file path (omit for whole-repo history)",
            },
            "max_entries": {
                "type": "integer",
                "description": "Maximum commits to return",
                "default": 5,
            },
        },
        "required": [],
    },
}

_GET_FUNCTION_SOURCE_SCHEMA = {
    "name": "get_function_source",
    "description": "Get the full source code of a specific function or method.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to the source file",
            },
            "function_name": {
                "type": "string",
                "description": "Name of the function or method",
            },
            "class_name": {
                "type": "string",
                "description": "Containing class name (required for methods)",
            },
        },
        "required": ["file_path", "function_name"],
    },
}

_GET_FILE_OUTLINE_SCHEMA = {
    "name": "get_file_outline",
    "description": (
        "Get the structural outline of a Python file: "
        "classes, methods, functions, and their line ranges."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to the Python file",
            },
        },
        "required": ["file_path"],
    },
}

# ---------------------------------------------------------------------------
# Public registry  {name: (callable, schema_dict)}
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, tuple] = {
    # ── Core file / search tools (used by 2+ agents) ──
    "code_search":          (code_search,                    _CODE_SEARCH_SCHEMA),
    "read_file":            (read_file,                      _READ_FILE_SCHEMA),
    "list_directory":       (list_directory,                 _LIST_DIRECTORY_SCHEMA),
    # ── Semantic / RAG search ──
    "semantic_search":      (semantic_search_formatted,      _SEMANTIC_SEARCH_SCHEMA),
    "semantic_file_search": (semantic_file_search_formatted, FILE_SEARCH_TOOL_DESCRIPTION),
    # ── Code structure ──
    "get_function_source":  (get_function_source,            _GET_FUNCTION_SOURCE_SCHEMA),
    "get_file_outline":     (get_file_outline,               _GET_FILE_OUTLINE_SCHEMA),
    # ── Git history ──
    "git_log":              (git_log_formatted,              _GIT_LOG_SCHEMA),
    # ── Log parsing ──
    "parse_logs":           (parse_logs_tool,                _LOG_TOOL_DESC),
    # ── Graph RAG ──
    "graph_search":         (agent_graph_search,             AGENT_GRAPH_SEARCH_TOOL),
    "find_callers":         (agent_find_callers,             AGENT_FIND_CALLERS_TOOL),
    "find_callees":         (agent_find_callees,             AGENT_FIND_CALLEES_TOOL),
}

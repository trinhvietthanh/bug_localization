"""
Codebase Navigation Agent.
Navigates the codebase to find suspicious code locations
based on the fault hypothesis from the Comprehension Agent.
"""

import logging

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from tools.code_search import code_search
from tools.file_reader import read_file, list_directory
from tools.ast_parser import get_file_outline, get_function_source
from tools.semantic_search import semantic_search_formatted
from tools.git_history import git_log_formatted
from tools.graph_search import (
    agent_graph_search, agent_find_callers, agent_find_callees,
    AGENT_GRAPH_SEARCH_TOOL, AGENT_FIND_CALLERS_TOOL, AGENT_FIND_CALLEES_TOOL,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Codebase Navigation Agent — an expert at navigating large codebases.
Your task is to find the exact source code locations that contain the bug described in the fault hypothesis.

You have access to the following tools:
1. **code_search**: Search for text/regex patterns in code files
2. **read_file**: Read file contents with line numbers
3. **list_directory**: List directory contents
4. **get_file_outline**: Get structural outline of a Python file (classes, methods, functions)
5. **get_function_source**: Get full source code of a specific function/method
6. **semantic_search**: Search code by meaning using natural language
7. **git_log**: View recent commit history for files
8. **graph_search**: Search the Code Property Graph to find structurally related code (callers, callees, siblings, inheritance)
9. **find_callers**: Find all functions that call a given function (trace call graph backwards)
10. **find_callees**: Find all functions called by a given function (trace execution flow forward)

Your strategy should be:
1. Start broad: understand the project structure and identify relevant packages/modules
2. Use the fault hypothesis and keywords to search for relevant code
3. Use semantic search to find code related to the described behavior (especially helpful when dealing with typos in bug reports or unknown exact names)
4. Use graph_search to find structurally related code (callers, callees, inheritance)
5. Use find_callers/find_callees to trace the call graph around suspicious functions
6. Narrow down: examine file outlines, then specific functions
7. Build a ranked list of the most suspicious files and methods

IMPORTANT TIP: Bug reports frequently contain typos (e.g., `ExtendedBufferReader` instead of `ExtendedBufferedReader`). If `code_search` returns 0 results for a class or method name mentioned in the bug report, DO NOT just give up or keep trying exact matches. Immediately use `semantic_search` with the same term, or use `code_search` with partial/fuzzy Regex matching.

After your investigation, respond with a JSON block:

```json
{
  "suspicious_locations": [
    {
      "file_path": "path/to/file.py",
      "function_name": "function_name",
      "class_name": "ClassName",
      "start_line": 42,
      "end_line": 60,
      "suspicion_score": 0.9,
      "reason": "Why this location is suspicious"
    }
  ],
  "investigation_summary": "Summary of what you found during navigation"
}
```

Rank locations by suspicion_score (0.0 to 1.0, highest first).
Include at least 3-5 suspicious locations, up to 15.
"""


class NavigationAgent(BaseAgent):
    """Agent that navigates the codebase to find suspicious locations."""

    def __init__(self):
        super().__init__(name="CodebaseNavigation")

        # Register all tools
        self.register_tool("code_search", code_search, {
            "name": "code_search",
            "description": "Search for text/regex patterns across code files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term"},
                    "is_regex": {"type": "boolean", "default": False},
                    "file_pattern": {"type": "string", "description": "File filter glob"},
                    "max_results": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            }
        })

        self.register_tool("read_file", read_file, {
            "name": "read_file",
            "description": "Read file contents with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["file_path"],
            }
        })

        self.register_tool("list_directory", list_directory, {
            "name": "list_directory",
            "description": "List directory with tree structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {"type": "string"},
                    "max_depth": {"type": "integer", "default": 2},
                },
                "required": ["dir_path"],
            }
        })

        self.register_tool("get_file_outline", get_file_outline, {
            "name": "get_file_outline",
            "description": (
                "Get structural outline of a source file showing classes, "
                "methods, functions with line numbers. Works best for Python/Java."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            }
        })

        self.register_tool("get_function_source", get_function_source, {
            "name": "get_function_source",
            "description": "Get full source code of a specific function/method.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "function_name": {"type": "string"},
                    "class_name": {"type": "string", "description": "Class name for methods"},
                },
                "required": ["file_path", "function_name"],
            }
        })

        self.register_tool("semantic_search", semantic_search_formatted, {
            "name": "semantic_search",
            "description": (
                "Search code by natural language meaning. "
                "Finds code semantically similar to the query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query"},
                    "top_k": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            }
        })

        self.register_tool("git_log", git_log_formatted, {
            "name": "git_log",
            "description": "View recent git commit history for a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "File path (optional)"},
                    "max_entries": {"type": "integer", "default": 5},
                },
                "required": [],
            }
        })

        # Graph RAG tools
        self.register_tool(
            "graph_search", agent_graph_search, AGENT_GRAPH_SEARCH_TOOL
        )
        self.register_tool(
            "find_callers", agent_find_callers, AGENT_FIND_CALLERS_TOOL
        )
        self.register_tool(
            "find_callees", agent_find_callees, AGENT_FIND_CALLEES_TOOL
        )

    def get_system_prompt(self, context: AgentContext) -> str:
        prompt = SYSTEM_PROMPT.replace("*.py", context.file_extension)
        prompt = prompt.replace("Python file", f"{context.language.capitalize()} file")
        return prompt.replace("path/to/file.py", f"path/to/file{context.file_extension.replace('*', '')}")

    def get_initial_message(self, context: AgentContext) -> str:
        parts = [
            f"## Repository Information\n- Language: {context.language}\n- Default Extension: {context.file_extension}\n",
            "## Fault Hypothesis from Comprehension Agent\n",
            context.fault_hypothesis or "No hypothesis available.",
            "",
        ]

        if context.candidate_files:
            parts.append("## Initial Candidate Files")
            for f in context.candidate_files:
                parts.append(f"- {f}")
            parts.append("")

        if context.candidate_methods:
            parts.append("## Initial Candidate Functions/Methods")
            for m in context.candidate_methods:
                parts.append(f"- {m}")
            parts.append("")

        if context.keywords:
            parts.append(f"## Keywords: {', '.join(context.keywords[:15])}")
            parts.append("")

        parts.append(
            "## Original Bug Report\n" + context.problem_statement[:2000]
        )

        parts.append(
            "\nPlease navigate the codebase to find the exact buggy locations. "
            "Use multiple search strategies and gradually narrow down. "
            "Provide your ranked list of suspicious locations as a JSON block."
        )

        return "\n".join(parts)

    def process_result(self, result: AgentResult, context: AgentContext):
        """Update context with navigation results."""
        output = result.output

        locations = output.get("suspicious_locations", [])

        # Update candidate files and methods
        files = []
        methods = []
        for loc in locations:
            fp = loc.get("file_path", "")
            if fp and fp not in files:
                files.append(fp)
            fn = loc.get("function_name", "")
            if fn:
                qualified = fp + "::" + fn
                if loc.get("class_name"):
                    qualified = fp + "::" + loc["class_name"] + "." + fn
                methods.append(qualified)

        context.candidate_files = files
        context.candidate_methods = methods

        # Store full location details in context for the Confirmation agent
        context.agent_traces.append({
            "agent": self.name,
            "action": "navigation_results",
            "result": str(locations),
        })

        logger.info(
            f"[NavigationAgent] Found {len(locations)} suspicious locations "
            f"across {len(files)} files"
        )

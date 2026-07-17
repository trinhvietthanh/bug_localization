"""
Codebase Navigation Agent.
Navigates the codebase to find suspicious code locations
based on the fault hypothesis from the Comprehension Agent.
"""

import logging

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Codebase Navigation Agent — an expert at navigating large codebases.
Your task is to find the exact source code locations that contain the bug described in the fault hypothesis.

You have access to the following tools:
1. **code_search**: Search for text/regex patterns in code files
2. **read_file**: Read file contents with line numbers
3. **list_directory**: List directory contents
4. **get_file_outline**: Get structural outline of a Python file (classes, methods, functions)
5. **get_function_source**: Get full source code of a specific function/method
6. **semantic_file_search**: Find the most relevant SOURCE FILES for a query (file-level, fast GFI)
7. **semantic_search**: Search code by meaning at method/class level; supports language_filter and package_filter
8. **git_log**: View recent commit history for files
9. **graph_search**: Search the Code Property Graph (callers, callees, siblings, inheritance)
10. **find_callers**: Find all functions that call a given function
11. **find_callees**: Find all functions called by a given function

Your strategy should be:
1. **File identification first**: Use `semantic_file_search` to get the top-K most relevant files
   before reading any code. This fast structural pass prevents wasted reads.
2. Search for exact class/method/file names from the bug report and stack traces with `code_search`.
3. Search for literal error messages and exception text.
4. Use `semantic_search` (with `language_filter` or `package_filter` for Java) for behavioral descriptions.
5. Use `graph_search` / `find_callers` / `find_callees` to trace call paths.
6. Read narrowed files/methods and produce ranked suspicious locations.
7. ALWAYS output full repo-relative paths (e.g., src/main/java/org/.../Foo.java).
8. If you find a suspicious method, inspect both its callers and callees.

IMPORTANT TIP: Bug reports frequently contain typos (e.g., `ExtendedBufferReader` instead of
`ExtendedBufferedReader`). If `code_search` returns 0 results, immediately use `semantic_search`
with the same term, or use regex with partial matching.

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

    # Tools sourced from the central registry
    TOOLS = [
        "code_search", "read_file", "list_directory",
        "get_function_source",
        "semantic_search", "semantic_file_search",
        "git_log",
        "graph_search", "find_callers", "find_callees",
    ]

    def __init__(self):
        super().__init__(name="CodebaseNavigation")
        for name in self.TOOLS:
            self.register_tool(name, *TOOL_REGISTRY[name])

    def _register_language_tools(self, language: str):
        """Register language-specific tools on first use."""
        if language.lower() == "python" and "get_file_outline" not in self.tools:
            self.register_tool("get_file_outline", *TOOL_REGISTRY["get_file_outline"])

    def get_system_prompt(self, context: AgentContext) -> str:
        prompt = SYSTEM_PROMPT.replace("*.py", context.file_extension)
        prompt = prompt.replace("Python file", f"{context.language.capitalize()} file")
        return prompt.replace(
            "path/to/file.py", f"path/to/file{context.file_extension.replace('*', '')}"
        )

    def get_initial_message(self, context: AgentContext) -> str:
        self._register_language_tools(context.language)

        project_hint = ""
        if context.instance_id and "_" in context.instance_id:
            project = context.instance_id.split("_")[0]
            try:
                from data.defects4j_loader import D4J_PROJECTS

                _d4j_project_names = set(D4J_PROJECTS.keys())
            except ImportError:
                _d4j_project_names = {
                    "Lang",
                    "Math",
                    "Chart",
                    "Closure",
                    "Mockito",
                    "Time",
                }
            if project in _d4j_project_names:
                project_hint = (
                    f"\n**IMPORTANT**: Focus your search EXCLUSIVELY on the '{project}' project. "
                    f"Do NOT waste time searching in other projects. "
                    f"If you find yourself looking at files from other projects, immediately redirect your search."
                )

        repo_info = f"- Language: {context.language}\n- Default Extension: {context.file_extension}"
        if context.source_root:
            repo_info += f"\n- Source Root: `{context.source_root}/` (all source .java files are under this directory)"
        parts = [
            f"## Repository Information\n{repo_info}\n",
            "## Fault Hypothesis from Comprehension Agent\n",
            context.fault_hypothesis or "No hypothesis available.",
            "",
        ]

        # E1: expose the full competing-hypothesis set so navigation can
        # cover all suspected components, not just the top hypothesis
        if context.hypotheses:
            parts.append("## Competing Hypotheses (explore ALL suspected areas)")
            for h in context.hypotheses:
                files = ", ".join(h.suspected_files[:3]) or "no files listed"
                parts.append(
                    f"- {h.hid} (prior {h.prior:.2f}): {h.statement} → {files}"
                )
            parts.append("")

        if context.test_derived_candidates:
            parts.append("## Test-Derived Candidate Files (HIGH PRIORITY)\n")
            parts.append(
                "Inferred from failing test class names — likely the buggy source files.\n"
            )
            for f in context.test_derived_candidates:
                parts.append(f"- {f}")
            parts.append("")

        if context.stack_trace_files:
            parts.append("## Stack Trace Files (START HERE - Highest Priority)\n")
            parts.append(
                "These files are directly from the stack trace. Investigate them FIRST.\n"
            )
            for i, f in enumerate(context.stack_trace_files[:10], 1):
                parts.append(f"{i}. {f}")
            parts.append("")

        if context.error_messages:
            parts.append("## Error Messages (Search for these)\n")
            for err in context.error_messages[:5]:
                parts.append(f"- {err}")
            parts.append("")

        if context.candidate_files:
            parts.append("## Initial Candidate Files")
            for f in context.candidate_files:
                stack_marker = (
                    " [STACK TRACE]" if f in context.stack_trace_files else ""
                )
                parts.append(f"- {f}{stack_marker}")
            parts.append("")

        if context.candidate_methods:
            parts.append("## Initial Candidate Functions/Methods")
            for m in context.candidate_methods:
                parts.append(f"- {m}")
            parts.append("")

        if context.keywords:
            parts.append(f"## Keywords: {', '.join(context.keywords[:15])}")
            parts.append("")

        if context.reflection_feedback:
            parts.append("## Reflection Feedback From Previous Round")
            parts.append(context.reflection_feedback[:3000])
            parts.append("")

        parts.append("## Original Bug Report\n" + context.problem_statement[:2000])

        if project_hint:
            parts.append(project_hint)
            parts.append("")

        priority_instruction = ""
        if context.stack_trace_files:
            priority_instruction = (
                "CRITICAL: Start by investigating stack trace files - they have the highest probability "
                "of containing the bug. Read them carefully before exploring other candidates. "
            )

        parts.append(
            f"\nPlease navigate the codebase to find the exact buggy locations. "
            f"{priority_instruction}"
            f"Use multiple search strategies and gradually narrow down. "
            f"Provide your ranked list of suspicious locations as a JSON block."
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

        # Navigation's findings go first, but keep earlier candidates (e.g.
        # stack-trace/log-analysis seeds) behind them instead of dropping them.
        prior = [f for f in context.candidate_files if f not in files]
        context.candidate_files = files + prior
        context.candidate_methods = methods

        # Replace prior navigation blob so Confirmation sees this round only
        context.agent_traces = [
            t for t in context.agent_traces if t.get("action") != "navigation_results"
        ]
        context.agent_traces.append(
            {
                "agent": self.name,
                "action": "navigation_results",
                "result": str(locations),
            }
        )

        logger.info(
            f"[NavigationAgent] Found {len(locations)} suspicious locations "
            f"across {len(files)} files"
        )

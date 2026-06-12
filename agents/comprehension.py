"""
Fault Comprehension Agent.
Analyzes bug reports to understand the nature of the fault
and generate hypotheses about its cause and location.
"""

import logging
from pathlib import Path

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


def search_tests(
    query: str,
    repo_path: str,
    is_regex: bool = False,
    max_results: int = 15,
) -> str:
    """
    Search specifically in test files for relevant test cases.
    Unlike code_search, this function INCLUDES test directories.
    Returns formatted results as a string.
    """
    import os
    import re as _re

    TEST_EXTENSIONS = {".py", ".java", ".js", ".ts"}
    TEST_DIR_HINTS = {"test", "tests", "testing", "__tests__", "spec"}

    flags = _re.IGNORECASE
    try:
        pattern = _re.compile(query if is_regex else _re.escape(query), flags)
    except _re.error:
        return f"Error: invalid query pattern '{query}'"

    results = []
    repo = Path(repo_path)
    for root, dirs, files in os.walk(repo):
        dirs[:] = [
            d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv"}
        ]
        rel_root = Path(root).relative_to(repo)
        is_test_dir = any(part.lower() in TEST_DIR_HINTS for part in rel_root.parts)
        for fname in files:
            fp = Path(root) / fname
            is_test_file = (
                is_test_dir
                or fname.lower().startswith("test_")
                or fname.lower().endswith(("_test.py", "test.java", "tests.java"))
            )
            if not is_test_file or fp.suffix not in TEST_EXTENSIONS:
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    rel = str(fp.relative_to(repo))
                    results.append(f"► {rel}:{i}: {line.strip()}")
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break

    if not results:
        return f"No test matches found for '{query}'"
    return "\n".join(results)


def _normalize_suspicious_file_entry(entry) -> str | None:
    """LLMs sometimes emit objects instead of plain path strings."""
    if isinstance(entry, str):
        s = entry.strip()
        return s if s else None
    if isinstance(entry, dict):
        for key in ("file_path", "path", "filepath", "file"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


SYSTEM_PROMPT = """You are a Fault Comprehension Agent — an expert software debugger with deep understanding of software engineering principles and common bug patterns.
Your task is to analyze a bug report and understand the nature of the fault with precision.

You have access to the following tools:
1. **code_search**: Search for text patterns in the codebase (skips test directories)
2. **read_file**: Read the contents of a source file  
3. **list_directory**: List files in a directory
4. **search_tests**: Search specifically within test files to understand expected behavior and identify which tests are failing — valuable for understanding the bug's context

Your goal is to:
1. Carefully read and understand the bug report - identify what the system should do vs what it actually does
2. Identify the type of error (logic bug, runtime error, API misuse, configuration issue, regression, performance issue, etc.) with justification
3. Extract ALL key information: error messages, stack traces, mentioned files/functions, variable names, API calls
4. If Java stack traces exist, precisely map package/class names to likely file paths in the repository structure
5. Use tools strategically to explore the codebase: first understand project structure, then search for relevant components
6. Formulate a DETAILED "fault hypothesis" — your best educated guess about:
   - EXACTLY which component/module is likely affected (include package hierarchy if deducible)
   - SPECIFICALLY what kind of code change would fix this (e.g., "fix off-by-one in loop condition", "add null pointer check", "correct method call ordering")
   - PRIORITIZED list of files/functions to investigate further with reasoning for each
   - CONFIDENCE level in your hypothesis with justification

BE THOROUGH and SPECIFIC in your analysis. Vague hypotheses lead to poor navigation. Consider:
- Common bug patterns: off-by-one errors, null pointer dereferences, resource leaks, race conditions, incorrect API usage
- Context clues: recent changes mentioned, configuration specifics, environmental factors
- Distinguish between root cause and symptoms

After your analysis, respond with a JSON block containing your findings:

```json
{
  "bug_summary": "Brief but precise summary of what the bug is and its impact",
  "bug_type": "logic_error|runtime_error|api_misuse|configuration|regression|performance|security|other",
  "key_components": ["list of relevant modules/packages with versions if mentioned"],
  "suspicious_files": ["list of SPECIFIC files to investigate with brief rationale for each"],
  "suspicious_functions": ["list of SPECIFIC functions/methods to investigate with brief rationale for each"],
  "fault_hypothesis": "EXTREMELY DETAILED hypothesis about the root cause including: suspected file, function, line characteristics, and exact nature of the fault",
  "search_keywords": ["PRIORITIZED keywords for further investigation - put most specific terms first"],
  "hypothesis_confidence": 0.0
}
```
"""


class ComprehensionAgent(BaseAgent):
    """Agent that analyzes bug reports to understand fault nature."""

    # Tools sourced from the central registry
    TOOLS = ["code_search", "read_file", "list_directory", "parse_logs"]

    def __init__(self):
        super().__init__(name="FaultComprehension")

        for name in self.TOOLS:
            self.register_tool(name, *TOOL_REGISTRY[name])

        # search_tests is agent-specific (not in registry) — register manually
        self.register_tool(
            "search_tests",
            search_tests,
            {
                "name": "search_tests",
                "description": (
                    "Search specifically within test files for relevant test cases related to the bug. "
                    "Unlike code_search, this INCLUDES test directories. "
                    "Use this to understand expected behavior and identify failing test scenarios."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search term or regex to find in test files",
                        },
                        "is_regex": {
                            "type": "boolean",
                            "description": "Whether query is a regex pattern",
                            "default": False,
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results",
                            "default": 15,
                        },
                    },
                    "required": ["query"],
                },
            },
        )

    def run(self, context: AgentContext, max_iterations: int = None) -> AgentResult:
        """Pre-extract structured bug info, then run the main agentic loop."""
        from config import config
        if config.enable_structured_bug_extraction:
            context.structured_bug_info = self._extract_structured_bug_info(
                context.problem_statement
            )
        return super().run(context, max_iterations)

    def _extract_structured_bug_info(self, problem_statement: str) -> dict:
        """
        Single lightweight LLM call (no tools) to extract structured bug components.
        Returns dict with bug_phenomenon, bug_explanation, bug_traceback keys.
        Falls back to empty dict on any failure.
        """
        if not problem_statement:
            return {}
        truncated = problem_statement[:3000]
        extraction_prompt = (
            "Extract the following from this bug report. Be concise.\n\n"
            f"Bug Report:\n{truncated}\n\n"
            "Return ONLY valid JSON with these exact keys:\n"
            '{"bug_phenomenon": "observable symptoms - what fails, error message, wrong output",'
            ' "bug_explanation": "developer explanation of why it might happen (empty string if not mentioned)",'
            ' "bug_traceback": "stack trace or code snippet verbatim (empty string if not present)"}'
        )
        try:
            response = self._call_llm([
                {"role": "system", "content": "You are a bug report analyst. Return only valid JSON."},
                {"role": "user", "content": extraction_prompt},
            ])
            content = response.choices[0].message.content or ""
            result = self._parse_output(content)
            if "bug_phenomenon" in result:
                logger.debug(f"[ComprehensionAgent] Structured extraction: {result}")
                return result
        except Exception as exc:
            logger.debug(f"Structured bug extraction failed: {exc}")
        return {}

    def get_system_prompt(self, context: AgentContext) -> str:
        prompt = SYSTEM_PROMPT.replace("*.py", context.file_extension)
        return prompt.replace(
            "path/to/file.py", f"path/to/file{context.file_extension.replace('*', '')}"
        )

    def get_initial_message(self, context: AgentContext) -> str:
        repo_info = f"- Language: {context.language}\n- Default Extension: {context.file_extension}"
        if context.source_root:
            repo_info += f"\n- Source Root: `{context.source_root}/` (search here for .java files)"
        parts = [
            f"## Repository Information\n{repo_info}\n",
            f"## Bug Report\n\n{context.problem_statement}\n",
        ]

        sbi = context.structured_bug_info
        if sbi:
            sbi_parts = []
            if sbi.get("bug_phenomenon"):
                sbi_parts.append(f"**Phenomenon (what fails):** {sbi['bug_phenomenon']}")
            if sbi.get("bug_explanation"):
                sbi_parts.append(f"**Explanation (why):** {sbi['bug_explanation']}")
            if sbi.get("bug_traceback"):
                sbi_parts.append(
                    f"**Traceback/Code:**\n```\n{sbi['bug_traceback'][:800]}\n```"
                )
            if sbi_parts:
                parts.append(
                    "## Structured Bug Analysis (Pre-extracted)\n" + "\n".join(sbi_parts)
                )

        if context.error_messages:
            parts.append(
                f"## Extracted Error Messages\n"
                + "\n".join(f"- {e}" for e in context.error_messages)
            )

        if context.test_derived_candidates:
            parts.append(
                "## Test-Derived Candidate Files (HIGH PRIORITY)\n"
                "These files were inferred from the failing test class names in the bug report "
                "(e.g. `WeekTests` → `Week.java`). Strongly prefer these as starting points.\n"
                + "\n".join(f"- {f}" for f in context.test_derived_candidates)
            )

        if context.stack_trace_files:
            parts.append(
                f"## Stack Trace Files (HIGH PRIORITY)\n"
                f"These files are directly linked to the crash. They should be top candidates.\n"
                + "\n".join(f"- {f}" for f in context.stack_trace_files)
            )

        if context.stack_traces:
            parts.append(f"## Raw Stack Traces\n" + "\n".join(context.stack_traces[:3]))

        if context.mentioned_files:
            parts.append(
                f"## Files Mentioned in Bug Report\n"
                + "\n".join(f"- {f}" for f in context.mentioned_files)
            )

        if context.mentioned_functions:
            # Separate log-extracted methods from plain mentions
            log_methods = [f for f in context.mentioned_functions if "#" in f]
            plain_funcs = [f for f in context.mentioned_functions if "#" not in f]
            if log_methods:
                parts.append(
                    "## Log Analysis: Methods in Stack Trace (HIGHEST PRIORITY)\n"
                    "These ClassName#methodName pairs were extracted directly from the "
                    "stack trace in the bug report — they are the most likely fault locations.\n"
                    + "\n".join(f"- {f}" for f in log_methods[:10])
                )
            if plain_funcs:
                parts.append(
                    "## Functions/Methods Mentioned in Bug Report\n"
                    + "\n".join(f"- {f}" for f in plain_funcs[:10])
                )

        # Drain3 log analysis
        lpr = getattr(context, "log_parse_result", None)
        if lpr:
            log_summary = lpr.summary()
            if log_summary.strip():
                parts.append("## Log Analysis (Drain3)\n" + log_summary)

        if context.repo_skeleton:
            parts.append("## Repository Skeleton (compact)")
            parts.append(context.repo_skeleton[:3000])

        priority_note = ""
        if context.stack_trace_files:
            priority_note = (
                " IMPORTANT: The stack trace files listed above have the highest probability "
                "of containing the bug. Include them as your top suspicious files. "
            )

        parts.append(
            f"\nPlease analyze this bug report. Use the tools to explore the "
            f"codebase and understand the project structure. For Java reports, "
            f"extract package/class paths from stack traces and map them to likely "
            f"repo file paths.{priority_note}"
            f"Then provide your fault hypothesis as a JSON block."
        )

        return "\n\n".join(parts)

    def process_result(self, result: AgentResult, context: AgentContext):
        """Update context with comprehension results."""
        output = result.output

        if "fault_hypothesis" in output:
            context.fault_hypothesis = output["fault_hypothesis"]

        if "suspicious_files" in output:
            raw = output["suspicious_files"]
            files: list[str] = []
            if isinstance(raw, list):
                for e in raw:
                    p = _normalize_suspicious_file_entry(e)
                    if p and p not in files:
                        files.append(p)
            elif isinstance(raw, str):
                p = _normalize_suspicious_file_entry(raw)
                if p:
                    files = [p]
            context.candidate_files = files

        if "suspicious_functions" in output:
            context.candidate_methods = output["suspicious_functions"]

        if "search_keywords" in output:
            context.keywords.extend(output.get("search_keywords", []))

        logger.info(
            f"[ComprehensionAgent] Hypothesis: {context.fault_hypothesis[:200]}"
        )
        logger.info(f"[ComprehensionAgent] Candidate files: {context.candidate_files}")

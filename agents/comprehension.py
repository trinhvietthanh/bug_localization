"""
Fault Comprehension Agent.
Analyzes bug reports to understand the nature of the fault
and generate hypotheses about its cause and location.
"""

import logging

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from tools.code_search import code_search
from tools.file_reader import read_file, list_directory

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Fault Comprehension Agent — an expert software debugger with deep understanding of software engineering principles and common bug patterns.
Your task is to analyze a bug report and understand the nature of the fault with precision.

You have access to the following tools:
1. **code_search**: Search for text patterns in the codebase
2. **read_file**: Read the contents of a source file  
3. **list_directory**: List files in a directory

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

    def __init__(self):
        super().__init__(name="FaultComprehension")

        # Register tools
        self.register_tool(
            "code_search",
            code_search,
            {
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
                            "description": "Search term or regex",
                        },
                        "is_regex": {
                            "type": "boolean",
                            "description": "Is regex?",
                            "default": False,
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "File glob filter, e.g. '{file_extension}'",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            },
        )

        self.register_tool(
            "read_file",
            read_file,
            {
                "name": "read_file",
                "description": "Read contents of a source file with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative file path",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Start line (1-indexed)",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "End line (inclusive)",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        )

        self.register_tool(
            "list_directory",
            list_directory,
            {
                "name": "list_directory",
                "description": "List directory contents with tree structure.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dir_path": {
                            "type": "string",
                            "description": "Relative dir path",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Max depth",
                            "default": 2,
                        },
                    },
                    "required": ["dir_path"],
                },
            },
        )

    def get_system_prompt(self, context: AgentContext) -> str:
        prompt = SYSTEM_PROMPT.replace("*.py", context.file_extension)
        return prompt.replace(
            "path/to/file.py", f"path/to/file{context.file_extension.replace('*', '')}"
        )

    def get_initial_message(self, context: AgentContext) -> str:
        parts = [
            f"## Repository Information\n- Language: {context.language}\n- Default Extension: {context.file_extension}\n",
            f"## Bug Report\n\n{context.problem_statement}\n",
        ]

        if context.error_messages:
            parts.append(
                f"## Extracted Error Messages\n"
                + "\n".join(f"- {e}" for e in context.error_messages)
            )

        if context.stack_traces:
            parts.append(f"## Stack Traces\n" + "\n".join(context.stack_traces[:3]))

        if context.mentioned_files:
            parts.append(
                f"## Files Mentioned in Bug Report\n"
                + "\n".join(f"- {f}" for f in context.mentioned_files)
            )

        if context.mentioned_functions:
            parts.append(
                f"## Functions Mentioned\n"
                + "\n".join(f"- {f}" for f in context.mentioned_functions)
            )

        if context.repo_skeleton:
            parts.append("## Repository Skeleton (compact)")
            parts.append(context.repo_skeleton[:6000])

        parts.append(
            "\nPlease analyze this bug report. Use the tools to explore the "
            "codebase and understand the project structure. For Java reports, "
            "extract package/class paths from stack traces and map them to likely "
            "repo file paths. Then provide your fault hypothesis as a JSON block."
        )

        return "\n\n".join(parts)

    def process_result(self, result: AgentResult, context: AgentContext):
        """Update context with comprehension results."""
        output = result.output

        if "fault_hypothesis" in output:
            context.fault_hypothesis = output["fault_hypothesis"]

        if "suspicious_files" in output:
            context.candidate_files = output["suspicious_files"]

        if "suspicious_functions" in output:
            context.candidate_methods = output["suspicious_functions"]

        if "search_keywords" in output:
            context.keywords.extend(output.get("search_keywords", []))

        logger.info(
            f"[ComprehensionAgent] Hypothesis: {context.fault_hypothesis[:200]}"
        )
        logger.info(f"[ComprehensionAgent] Candidate files: {context.candidate_files}")

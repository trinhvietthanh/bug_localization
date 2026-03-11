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


SYSTEM_PROMPT = """You are a Fault Comprehension Agent — an expert software debugger.
Your task is to analyze a bug report and understand the nature of the fault.

You have access to the following tools:
1. **code_search**: Search for text patterns in the codebase
2. **read_file**: Read the contents of a source file  
3. **list_directory**: List files in a directory

Your goal is to:
1. Carefully read and understand the bug report
2. Identify the type of error (logic bug, runtime error, API misuse, etc.)
3. Extract key information: error messages, stack traces, mentioned files/functions
4. Use tools to explore the codebase for initial context
5. Formulate a "fault hypothesis" — your best guess about:
   - What component/module is likely affected
   - What kind of code change would fix this
   - Which files/functions to investigate further

After your analysis, respond with a JSON block containing your findings:

```json
{
  "bug_summary": "Brief summary of the bug",
  "bug_type": "logic_error|runtime_error|api_misuse|configuration|regression|other",
  "key_components": ["list of relevant modules/packages"],
  "suspicious_files": ["list of files to investigate"],
  "suspicious_functions": ["list of functions to investigate"],
  "fault_hypothesis": "Detailed hypothesis about the root cause",
  "search_keywords": ["keywords for further investigation"]
}
```
"""


class ComprehensionAgent(BaseAgent):
    """Agent that analyzes bug reports to understand fault nature."""

    def __init__(self):
        super().__init__(name="FaultComprehension")

        # Register tools
        self.register_tool("code_search", code_search, {
            "name": "code_search",
            "description": (
                "Search for text or regex patterns across code files. "
                "Returns matching lines with file paths and line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term or regex"},
                    "is_regex": {"type": "boolean", "description": "Is regex?", "default": False},
                    "file_pattern": {"type": "string", "description": "File glob filter, e.g. '{file_extension}'"},
                    "max_results": {"type": "integer", "description": "Max results", "default": 20},
                },
                "required": ["query"],
            }
        })

        self.register_tool("read_file", read_file, {
            "name": "read_file",
            "description": "Read contents of a source file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Relative file path"},
                    "start_line": {"type": "integer", "description": "Start line (1-indexed)"},
                    "end_line": {"type": "integer", "description": "End line (inclusive)"},
                },
                "required": ["file_path"],
            }
        })

        self.register_tool("list_directory", list_directory, {
            "name": "list_directory",
            "description": "List directory contents with tree structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir_path": {"type": "string", "description": "Relative dir path"},
                    "max_depth": {"type": "integer", "description": "Max depth", "default": 2},
                },
                "required": ["dir_path"],
            }
        })

    def get_system_prompt(self, context: AgentContext) -> str:
        prompt = SYSTEM_PROMPT.replace("*.py", context.file_extension)
        return prompt.replace("path/to/file.py", f"path/to/file{context.file_extension.replace('*', '')}")

    def get_initial_message(self, context: AgentContext) -> str:
        parts = [
            f"## Repository Information\n- Language: {context.language}\n- Default Extension: {context.file_extension}\n",
            f"## Bug Report\n\n{context.problem_statement}\n",
        ]

        if context.error_messages:
            parts.append(f"## Extracted Error Messages\n" +
                        "\n".join(f"- {e}" for e in context.error_messages))

        if context.stack_traces:
            parts.append(f"## Stack Traces\n" +
                        "\n".join(context.stack_traces[:3]))

        if context.mentioned_files:
            parts.append(f"## Files Mentioned in Bug Report\n" +
                        "\n".join(f"- {f}" for f in context.mentioned_files))

        if context.mentioned_functions:
            parts.append(f"## Functions Mentioned\n" +
                        "\n".join(f"- {f}" for f in context.mentioned_functions))

        parts.append(
            "\nPlease analyze this bug report. Use the tools to explore the "
            "codebase and understand the project structure. Then provide your "
            "fault hypothesis as a JSON block."
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
        logger.info(
            f"[ComprehensionAgent] Candidate files: {context.candidate_files}"
        )

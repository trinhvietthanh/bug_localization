"""
Fault Confirmation Agent.
Reviews candidate locations and produces the final ranked list
with natural language explanations.
"""

import logging

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Fault Confirmation Agent — a meticulous code reviewer and debugger.
Your task is to review suspicious code locations identified by previous agents and confirm
which ones are most likely to contain the bug.

You have access to:
1. **code_search**: Search for text patterns in code
2. **read_file**: Read file contents with line numbers
3. **semantic_search**: Search code by natural language meaning
4. **find_callers**: Find all functions that call a given function (impact analysis)
5. **find_callees**: Find all functions called by a given function (trace execution flow)

For each candidate location:
1. Read the full source code of the suspicious function/method
2. Analyze the logic carefully against the bug description
3. Check for common bug patterns: off-by-one, wrong conditions, missing checks, null handling, type mismatches
4. Consider the data flow and control flow
5. Assess how well the code matches the reported behavior
6. For each top candidate, reason explicitly:
   (a) Does this code match the reported behavior?
   (b) Would changing this location plausibly fix the bug?
   (c) Is there a stronger alternative location?

CRITICAL RANKING RULES for Top-1 accuracy:
- Files mentioned in stack traces or error messages should be STRONGLY preferred for rank 1
- Assign HIGH confidence (0.8+) ONLY when you find a clear bug that directly explains the reported behavior
- Assign MEDIUM confidence (0.5-0.79) when the code is suspicious but the bug pattern is not obvious
- Assign LOW confidence (<0.5) when the connection to the bug is speculative
- If multiple files could be buggy, prefer files that are actually executed (in stack traces) over inferred files
- DO NOT give high confidence to test files unless the bug is clearly in test setup/assertion logic

If the provided candidates are weak or inconclusive, do NOT just reorder them.
Actively discover new candidates using code_search and semantic_search, then validate them.
Follow callers/callees of suspicious methods to identify nearby true fault locations.

IMPORTANT TIP: Bug reports frequently contain typos (e.g., `ExtendedBufferReader` instead of `ExtendedBufferedReader`). If `code_search` returns 0 results for a class or method name mentioned in the bug report, DO NOT just give up or keep trying exact matches. Immediately use `semantic_search` with the same term, or use `code_search` with partial/fuzzy Regex matching.

After thorough review, respond with your FINAL ranked results as a JSON block:

```json
{
  "ranked_locations": [
    {
      "rank": 1,
      "file_path": "path/to/file.py",
      "function_name": "buggy_function",
      "class_name": "ClassName",
      "start_line": 42,
      "end_line": 60,
      "confidence": 0.95,
      "explanation": "Detailed explanation of why this is likely the buggy location and what the bug is"
    }
  ],
  "overall_analysis": "Summary of your debugging analysis",
  "root_cause": "Your best assessment of the root cause"
}
```

Be thorough and analytical. The rank 1 location should be your strongest candidate with honest confidence.
Include detailed explanations that justify the confidence score.
"""


class ConfirmationAgent(BaseAgent):
    """Agent that reviews and confirms suspicious locations."""

    # Tools sourced from the central registry
    TOOLS = [
        "code_search", "read_file",
        "semantic_search",
        "find_callers", "find_callees",
    ]

    def __init__(self):
        super().__init__(name="FaultConfirmation")
        for name in self.TOOLS:
            self.register_tool(name, *TOOL_REGISTRY[name])

    def get_system_prompt(self, context: AgentContext) -> str:
        return SYSTEM_PROMPT

    def get_initial_message(self, context: AgentContext) -> str:
        parts = [
            "## Bug Report\n",
            context.problem_statement[:2000],
            "",
            "## Fault Hypothesis\n",
            context.fault_hypothesis or "No hypothesis available.",
            "",
        ]

        if context.error_messages:
            parts.append("## Error Messages (HIGH PRIORITY - check these)\n")
            for err in context.error_messages[:5]:
                parts.append(f"- {err}")
            parts.append("")

        if context.stack_trace_files:
            parts.append("## Stack Trace Files (HIGHEST PRIORITY - start here)\n")
            parts.append(
                "These files are directly linked to the crash. Prioritize them for rank 1.\n"
            )
            for i, f in enumerate(context.stack_trace_files[:10], 1):
                parts.append(f"{i}. {f}")
            parts.append("")

        nav_results = None
        for trace in reversed(context.agent_traces):
            if trace.get("action") == "navigation_results":
                nav_results = trace.get("result", "")
                break

        if context.candidate_files:
            parts.append("## Candidate Files (ordered by suspicion)")
            for i, f in enumerate(context.candidate_files, 1):
                stack_marker = (
                    " [IN STACK TRACE]" if f in context.stack_trace_files else ""
                )
                parts.append(f"{i}. {f}{stack_marker}")
            parts.append("")

        if context.candidate_methods:
            parts.append("## Candidate Functions/Methods")
            for m in context.candidate_methods:
                parts.append(f"- {m}")
            parts.append("")

        if nav_results:
            parts.append("## Navigation Agent's Detailed Findings\n")
            parts.append(nav_results[:3000])
            parts.append("")

        parts.append(
            "\nPlease carefully review each candidate location by reading "
            "the actual source code. START with stack-trace-linked files if present - "
            "they have the highest probability of containing the bug. "
            "For each strong candidate, reason step by step: "
            "(1) match to reported behavior, (2) whether a fix here would address the bug, "
            "(3) whether another location is more likely. If candidates are weak, use "
            "code_search and semantic_search to find and rank NEW file paths before final JSON. "
            "Be conservative with confidence - only 0.8+ when you find a clear, obvious bug."
        )

        return "\n".join(parts)

    def process_result(self, result: AgentResult, context: AgentContext):
        """Extract final ranked locations from the result."""
        output = result.output

        ranked = output.get("ranked_locations", [])

        # Update context with final results
        if ranked:
            context.candidate_files = [
                loc["file_path"] for loc in ranked if loc.get("file_path")
            ]
            context.candidate_methods = []
            for loc in ranked:
                if loc.get("function_name"):
                    method = loc["file_path"]
                    if loc.get("class_name"):
                        method += "::" + loc["class_name"] + "." + loc["function_name"]
                    else:
                        method += "::" + loc["function_name"]
                    context.candidate_methods.append(method)

        logger.info(
            f"[ConfirmationAgent] Final ranking: {len(ranked)} locations confirmed"
        )
        if ranked:
            top = ranked[0]
            logger.info(
                f"[ConfirmationAgent] Top-1: {top.get('file_path', '?')} "
                f"({top.get('confidence', 0):.2f})"
            )

    def get_reflection_message(
        self, result: AgentResult, confidence_threshold: float
    ) -> str:
        """
        Produce reflection guidance for a retry round when confidence is low.
        """
        ranked = result.output.get("ranked_locations", []) if result.output else []
        top_conf = 0.0
        if ranked:
            try:
                top_conf = float(ranked[0].get("confidence", 0.0))
            except (TypeError, ValueError):
                top_conf = 0.0

        weak_candidates = [loc.get("file_path", "?") for loc in ranked[:5]]
        if weak_candidates:
            investigated = ", ".join(weak_candidates)
        else:
            investigated = "no convincing locations were validated"

        analysis = ""
        if result.output:
            oa = result.output.get("overall_analysis") or ""
            if isinstance(oa, str) and oa.strip():
                analysis = f" Prior analysis: {oa.strip()[:600]}"

        return (
            f"I could not confirm any strong candidate (top confidence={top_conf:.2f}, "
            f"threshold={confidence_threshold:.2f}). Investigated but inconclusive: "
            f"{investigated}.{analysis} Re-investigate with broader semantic search, prioritize "
            "stack-trace-linked files, and verify caller/callee chains around likely methods."
        )

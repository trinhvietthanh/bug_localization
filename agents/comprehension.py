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


_TREE_EXCLUDE_DIRS = {
    ".git", "__pycache__", ".tox", "build", "dist", "node_modules",
    "site-packages", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
    "migrations",
}
_TREE_EXCLUDE_PARTS = ("egg-info",)


def build_repo_file_tree(repo_path: str, extension: str = "*.py", cap: int = 2500) -> list[str]:
    """Repo-relative source paths, same exclusions as the bare-LLM baseline."""
    suffix = extension.replace("*", "")
    files: list[str] = []
    root = Path(repo_path)
    for p in sorted(root.rglob(f"*{suffix}")):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if set(rel.split("/")) & _TREE_EXCLUDE_DIRS:
            continue
        if any(ex in rel for ex in _TREE_EXCLUDE_PARTS):
            continue
        files.append(rel)
        if len(files) >= cap:
            break
    return files


def build_file_excerpt(repo_path: str, rel_path: str, head_lines: int = 25,
                       outline_cap: int = 50) -> str | None:
    """Compact evidence for the verify pass: file head + def/class outline."""
    import re as _re

    fp = Path(repo_path) / rel_path
    try:
        text = fp.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    lines = text.splitlines()
    head = lines[:head_lines]
    outline = []
    pat = _re.compile(r"^\s*(def |class |DEFAULT|[A-Z_]{4,}\s*=)")
    for i, line in enumerate(lines, 1):
        if pat.match(line):
            outline.append(f"L{i}: {line.strip()[:120]}")
            if len(outline) >= outline_cap:
                break
    parts = [f"### {rel_path} ({len(lines)} lines)"]
    if head:
        parts.append("```\n" + "\n".join(head) + "\n```")
    if outline:
        parts.append("Definitions:\n" + "\n".join(outline))
    return "\n".join(parts)


VERIFY_SHOT_PROMPT = """Below are outlines of your top suspect files. Re-examine your answer \
against this evidence before it is finalized.

CRITICAL: the correct answer is the file a FIX PATCH would edit — which is often NOT the file \
where the symptom appears. Behavior implemented in one layer is frequently fixed in another: \
a settings/defaults module, a serializer, a base class, a SQL compiler, a config registry. \
Ask yourself for your #1 file: "would the patch really edit THIS file, or does the responsible \
code live elsewhere in the repository file listing above?"

If the evidence supports your ranking, keep it unchanged. If it clearly points elsewhere, revise. \
Respond with the SAME complete JSON block (all fields).

"""


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
4. If tools are available, use them SPARINGLY — at most a few targeted searches or file reads to confirm a suspicion. Do NOT explore the codebase broadly; a dedicated navigation phase will do that after you. Your job is fast triage: answer as soon as you have enough information.
5. Formulate a DETAILED "fault hypothesis" — your best educated guess about:
   - EXACTLY which component/module is likely affected (include package hierarchy if deducible)
   - SPECIFICALLY what kind of code change would fix this (e.g., "fix off-by-one in loop condition", "add null pointer check", "correct method call ordering")
   - PRIORITIZED list of files/functions to investigate further with reasoning for each
   - CONFIDENCE level in your hypothesis with justification

BE SPECIFIC in your analysis — vague hypotheses lead to poor navigation — but do not trade speed for exhaustiveness. Consider:
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

# E1: appended to SYSTEM_PROMPT when ENABLE_HYPOTHESIS_LOOP is on.
# {K} is replaced with config.hypothesis_k.
HYPOTHESES_PROMPT_ADDON = """
ADDITIONALLY, include a "hypotheses" array in the same JSON block: {K} COMPETING, \
FALSIFIABLE hypotheses about the root cause.

"hypotheses": [
  {
    "hid": "HYP1",
    "statement": "one falsifiable sentence naming a MECHANISM, not a symptom",
    "suspected_component": "package/module name",
    "suspected_files": ["path/to/file.py"],
    "suspected_functions": ["Class.method"],
    "causal_chain": ["input X reaches f()", "f() assumes Y", "Y violated when Z", "observed symptom"],
    "probes": [
      {"description": "what to check", "check_type": "code_pattern",
       "expected_if_true": "what should be observed if this hypothesis holds",
       "query_hint": "read_file(path) or code_search(term)"},
      {"description": "DISCONFIRMING: what would prove this FALSE", "check_type": "code_pattern",
       "expected_if_true": "if we instead observe ..., this hypothesis is FALSE",
       "query_hint": "..."}
    ],
    "prior": 0.6
  }
]

RULES for hypotheses:
- Produce exactly {K} hypotheses (or fewer only if the bug genuinely admits fewer distinct causes).
- Each must name a DIFFERENT component or a DIFFERENT mechanism — no restatements.
- At least one probe per hypothesis must be able to DISPROVE it.
- Priors must NOT all exceed 0.7 — you are uncertain; that is why there are several.
- "fault_hypothesis" must be the statement of your top-prior hypothesis.
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
        """Single-shot comprehension with escalation to the tool loop.

        v2 flow: pre-extract structured bug info, then ONE tool-free call
        (bug report + file tree carry most of the signal — the bare-LLM
        baseline hits 64% Top-1 from those alone). The agentic tool loop
        runs only when the single shot is low-confidence or empty.
        """
        from config import config

        if max_iterations is None:
            max_iterations = config.comprehension_max_iterations

        extraction_calls = 0
        if config.enable_structured_bug_extraction:
            context.structured_bug_info = self._extract_structured_bug_info(
                context.problem_statement
            )
            extraction_calls = 1

        if not config.comprehension_single_shot:
            result = super().run(context, max_iterations)
            result.num_llm_calls += extraction_calls
            return result

        shot, shot_messages = self._run_single_shot(context)
        # Self-consistency check: self-reported confidence is uninformative
        # (0.95 flat), but a top-1 that survives a grounded second look is a
        # reliable stability signal — when it flips, the model is guessing
        # and the tool loop earns its cost.
        unstable = False
        if shot is not None and config.comprehension_verify_shot:
            top1_before = self._top_file(shot)
            self._verify_shot(shot_messages, shot.explanation, shot, context)
            unstable = self._top_file(shot) != top1_before
        if shot is not None and not unstable and not self._needs_escalation(shot, config):
            shot.num_llm_calls += extraction_calls
            return shot

        reason = (
            "failed" if shot is None
            else "unstable top-1 across verify pass" if unstable
            else "low-confidence"
        )
        logger.info(
            f"[ComprehensionAgent] single shot {reason} — "
            f"escalating to tool loop (max {max_iterations} iterations)"
        )
        loop_result = super().run(context, max_iterations)
        loop_result.num_llm_calls += extraction_calls
        if shot is not None:
            loop_result.num_llm_calls += shot.num_llm_calls
            loop_result.prompt_tokens += shot.prompt_tokens
            loop_result.completion_tokens += shot.completion_tokens
            loop_result.total_tokens += shot.total_tokens
            # Loop crashed (e.g. API error): the low-confidence single shot
            # still beats returning nothing
            if not loop_result.success and shot.success:
                shot.num_llm_calls = loop_result.num_llm_calls
                shot.prompt_tokens = loop_result.prompt_tokens
                shot.completion_tokens = loop_result.completion_tokens
                shot.total_tokens = loop_result.total_tokens
                return shot
        return loop_result

    def _run_single_shot(
        self, context: AgentContext
    ) -> tuple[AgentResult | None, list[dict]]:
        """One tool-free LLM call producing the full comprehension JSON.
        Returns the result plus the message list so the caller can run the
        verify pass as a continuation of the same conversation."""
        result = AgentResult(agent_name=self.name)
        messages = [
            {"role": "system", "content": self.get_system_prompt(context)},
            {"role": "user", "content": self.get_initial_message(context)},
            {
                "role": "user",
                "content": (
                    "Tools are NOT available in this pass. Based on the bug report "
                    "and the repository file listing above, respond NOW with the "
                    "required JSON block. Use repository-relative paths exactly as "
                    "they appear in the file listing."
                ),
            },
        ]
        try:
            response = self._call_llm(messages, context, use_tools=False)
        except Exception as e:
            logger.warning(f"[ComprehensionAgent] single-shot call failed: {e}")
            return None, messages
        result.num_llm_calls = 1
        usage = getattr(response, "usage", None)
        if usage:
            result.prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            result.total_tokens = getattr(usage, "total_tokens", 0) or 0
        content = response.choices[0].message.content or ""
        if not content.strip():
            return None, messages
        result.output = self._parse_output(content)
        result.explanation = content
        result.success = True
        context.add_trace(self.name, "single_shot_answer", content[:300])
        return result, messages

    @staticmethod
    def _top_file(result: AgentResult) -> str | None:
        for e in (result.output or {}).get("suspicious_files") or []:
            p = _normalize_suspicious_file_entry(e)
            if p:
                return p
        return None

    def _verify_shot(self, messages: list[dict], first_answer: str,
                     result: AgentResult, context: AgentContext) -> None:
        """One follow-up call grounding the ranking in actual file contents.
        Mutates `result` in place; keeps the first answer on any failure."""
        from config import config

        files: list[str] = []
        for e in (result.output or {}).get("suspicious_files") or []:
            p = _normalize_suspicious_file_entry(e)
            if p and p not in files:
                files.append(p)
            if len(files) >= config.comprehension_verify_files:
                break
        excerpts = []
        for rel in files:
            ex = build_file_excerpt(context.repo_path, rel)
            if ex:
                excerpts.append(ex)
        if not excerpts:
            return

        messages.append({"role": "assistant", "content": first_answer})
        messages.append(
            {"role": "user", "content": VERIFY_SHOT_PROMPT + "\n\n".join(excerpts)}
        )
        try:
            response = self._call_llm(messages, context, use_tools=False)
        except Exception as e:
            logger.warning(f"[ComprehensionAgent] verify shot failed: {e}")
            return
        result.num_llm_calls += 1
        usage = getattr(response, "usage", None)
        if usage:
            result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            result.total_tokens += getattr(usage, "total_tokens", 0) or 0
        content = response.choices[0].message.content or ""
        if not content.strip():
            return
        revised = self._parse_output(content)
        if any(
            _normalize_suspicious_file_entry(e)
            for e in revised.get("suspicious_files") or []
        ):
            result.output = revised
            result.explanation = content
            context.add_trace(self.name, "verify_shot_answer", content[:300])

    @staticmethod
    def _needs_escalation(result: AgentResult, config) -> bool:
        """Escalate when the single shot produced no usable candidates or is
        explicitly unsure about its own hypothesis."""
        output = result.output or {}
        files = []
        for e in output.get("suspicious_files") or []:
            p = _normalize_suspicious_file_entry(e)
            if p:
                files.append(p)
        if not files:
            return True
        try:
            conf = float(output.get("hypothesis_confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf > 1:  # some models answer on a 0–100 scale
            conf /= 100.0
        return conf < config.comprehension_escalation_conf

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
        from config import config

        prompt = SYSTEM_PROMPT
        if config.enable_hypothesis_loop:
            prompt += HYPOTHESES_PROMPT_ADDON.replace(
                "{K}", str(max(3, min(config.hypothesis_k, 5)))
            )
        prompt = prompt.replace("*.py", context.file_extension)
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

        from config import config as _config
        if _config.comprehension_file_tree and context.repo_path:
            try:
                tree = build_repo_file_tree(
                    context.repo_path,
                    context.file_extension,
                    cap=_config.comprehension_file_tree_max,
                )
            except Exception as e:
                logger.debug(f"[ComprehensionAgent] file tree unavailable: {e}")
                tree = []
            if tree:
                parts.append(
                    f"## Repository Source Files ({len(tree)})\n"
                    "Use these repository-relative paths EXACTLY as listed when "
                    "naming suspicious_files.\n" + "\n".join(tree)
                )

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

        self._process_hypotheses(output, context)

        logger.info(
            f"[ComprehensionAgent] Hypothesis: {context.fault_hypothesis[:200]}"
        )
        logger.info(f"[ComprehensionAgent] Candidate files: {context.candidate_files}")

    @staticmethod
    def _process_hypotheses(output: dict, context: AgentContext):
        """E1: build the hypothesis tracker from comprehension output."""
        from config import config

        if not config.enable_hypothesis_loop:
            return

        from agents.hypothesis import HypothesisTracker, parse_hypotheses_from_llm

        hypotheses = parse_hypotheses_from_llm(
            output.get("hypotheses"),
            fallback_statement=context.fault_hypothesis,
            max_k=max(3, min(config.hypothesis_k, 5)),
        )
        if not hypotheses:
            return

        context.hypotheses = hypotheses
        context.hypothesis_tracker = HypothesisTracker(
            hypotheses, falsify_threshold=config.hypothesis_falsify_threshold
        )

        # Contract: fault_hypothesis = top-prior hypothesis statement
        top = max(hypotheses, key=lambda h: h.prior)
        if top.statement:
            context.fault_hypothesis = top.statement

        # Union suspected files into candidates, top-hypothesis files first
        merged: list[str] = []
        for h in sorted(hypotheses, key=lambda x: x.prior, reverse=True):
            for fp in h.suspected_files:
                if fp and fp not in merged:
                    merged.append(fp)
        for fp in context.candidate_files:
            if fp and fp not in merged:
                merged.append(fp)
        context.candidate_files = merged
        logger.info(
            f"[ComprehensionAgent] {len(hypotheses)} competing hypotheses: "
            + "; ".join(f"{h.hid}(p={h.prior:.2f})" for h in hypotheses)
        )

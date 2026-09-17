"""
Fault Comprehension Agent.
Analyzes bug reports to understand the nature of the fault
and generate hypotheses about its cause and location.
"""

import logging
import os
from pathlib import Path

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from tools.registry import TOOL_REGISTRY
from utils.path_utils import coerce_path_string

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
    python_decl = _re.compile(r"^\s*(?:async\s+def|def|class)\s+\w+")
    java_type_decl = _re.compile(
        r"^\s*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*"
        r"(?:class|interface|enum|record)\s+\w+"
    )
    java_method_decl = _re.compile(
        r"^\s*(?:(?:public|protected|private|abstract|final|static|synchronized|native|default)\s+)+"
        r"(?:<[^>]+>\s+)?[\w.$<>?\[\],]+\s+\w+\s*\([^;]*\)\s*(?:throws\s+[^{]+)?(?:\{|;)?\s*$"
    )
    java_constructor_decl = _re.compile(
        r"^\s*(?:(?:public|protected|private)\s+)+\w+\s*\([^;]*\)\s*"
        r"(?:throws\s+[^{]+)?(?:\{.*)?$"
    )
    constant_decl = _re.compile(r"^\s*[A-Z_][A-Z0-9_]{3,}\s*=")
    for i, line in enumerate(lines, 1):
        if (
            python_decl.match(line)
            or java_type_decl.match(line)
            or java_method_decl.match(line)
            or java_constructor_decl.match(line)
            or constant_decl.match(line)
        ):
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


def _canonical_repo_path(path: str, repo_path: str = "") -> str | None:
    """Normalize an LLM-produced path without allowing parent traversal."""
    value = str(path or "").strip().strip("`\"'").replace("\\", "/")
    if not value:
        return None
    if repo_path:
        root = Path(repo_path).resolve()
        try:
            candidate = Path(value)
            if candidate.is_absolute():
                value = candidate.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return None
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    normalized = os.path.normpath(value).replace("\\", "/")
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        return None
    return normalized


def _normalize_suspicious_file_entry(entry, repo_path: str = "") -> str | None:
    """Coerce an LLM file entry (string or dict) to a canonical repo-relative path."""
    raw = coerce_path_string(entry)
    if raw is None:
        return None
    return _canonical_repo_path(raw, repo_path)


def _normalize_string_list(value, dict_keys: tuple[str, ...] = ()) -> list[str]:
    """Coerce a lenient LLM field into a deduplicated list of strings."""
    entries = value if isinstance(value, list) else [value] if value is not None else []
    normalized: list[str] = []
    for entry in entries:
        candidate = entry
        if isinstance(entry, dict):
            candidate = next(
                (entry.get(key) for key in dict_keys if entry.get(key)), None
            )
        if not isinstance(candidate, str):
            continue
        candidate = candidate.strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


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
  "suspicious_files": ["repository-relative/path.py"],
  "suspicious_functions": ["Class.method"],
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

        extraction_usage = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        if config.enable_structured_bug_extraction:
            context.structured_bug_info, extraction_usage = self._extract_structured_bug_info(
                context.problem_statement
            )

        if not config.comprehension_single_shot:
            result = super().run(context, max_iterations)
            self._merge_usage(result, extraction_usage)
            self._validate_result(result)
            self._apply_patch_owner_challenge(result, context, config)
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
            self._merge_usage(shot, extraction_usage)
            self._apply_patch_owner_challenge(shot, context, config)
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
        self._merge_usage(loop_result, extraction_usage)
        self._validate_result(loop_result)
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
                self._apply_patch_owner_challenge(shot, context, config)
                return shot
        self._apply_patch_owner_challenge(loop_result, context, config)
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
        raw_files = (result.output or {}).get("suspicious_files") or []
        if not isinstance(raw_files, list):
            raw_files = [raw_files]
        for e in raw_files:
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
        raw_files = (result.output or {}).get("suspicious_files") or []
        if not isinstance(raw_files, list):
            raw_files = [raw_files]
        for e in raw_files:
            p = _normalize_suspicious_file_entry(e, context.repo_path)
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
        revised_files = revised.get("suspicious_files") or []
        if not isinstance(revised_files, list):
            revised_files = [revised_files]
        if any(_normalize_suspicious_file_entry(e) for e in revised_files):
            result.output = revised
            result.explanation = content
            context.add_trace(self.name, "verify_shot_answer", content[:300])

    @staticmethod
    def _needs_escalation(result: AgentResult, config) -> bool:
        """Escalate when the single shot produced no usable candidates or is
        explicitly unsure about its own hypothesis."""
        output = result.output or {}
        files = []
        raw_files = output.get("suspicious_files") or []
        if not isinstance(raw_files, list):
            raw_files = [raw_files]
        for e in raw_files:
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

    @staticmethod
    def _merge_usage(result: AgentResult, usage: dict) -> None:
        result.num_llm_calls += usage.get("llm_calls", 0)
        result.prompt_tokens += usage.get("prompt_tokens", 0)
        result.completion_tokens += usage.get("completion_tokens", 0)
        result.total_tokens += usage.get("total_tokens", 0)

    @staticmethod
    def _has_usable_output(output: dict) -> bool:
        if not isinstance(output, dict):
            return False
        files = _normalize_string_list(
            output.get("suspicious_files"),
            ("file_path", "path", "filepath", "file"),
        )
        hypothesis = output.get("fault_hypothesis")
        return bool(
            files
            or isinstance(hypothesis, str) and hypothesis.strip()
            or isinstance(output.get("hypotheses"), list) and output["hypotheses"]
        )

    @classmethod
    def _validate_result(cls, result: AgentResult) -> None:
        if result.success and not cls._has_usable_output(result.output):
            result.success = False
            result.error = "Comprehension response contained no usable structured output"

    def _extract_structured_bug_info(self, problem_statement: str) -> tuple[dict, dict]:
        """
        Single lightweight LLM call (no tools) to extract structured bug components.
        Returns dict with bug_phenomenon, bug_explanation, bug_traceback keys.
        Falls back to empty dict on any failure.
        """
        if not problem_statement:
            return {}, {"llm_calls": 0, "prompt_tokens": 0,
                        "completion_tokens": 0, "total_tokens": 0}
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
            ], use_tools=False)
            api_usage = getattr(response, "usage", None)
            usage = {
                "llm_calls": 1,
                "prompt_tokens": getattr(api_usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(api_usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(api_usage, "total_tokens", 0) or 0,
            }
            content = response.choices[0].message.content or ""
            result = self._parse_output(content)
            if "bug_phenomenon" in result:
                logger.debug(f"[ComprehensionAgent] Structured extraction: {result}")
                return result, usage
            return {}, usage
        except Exception as exc:
            logger.debug(f"Structured bug extraction failed: {exc}")
        return {}, {"llm_calls": 1, "prompt_tokens": 0,
                    "completion_tokens": 0, "total_tokens": 0}

    def _apply_patch_owner_challenge(
        self, result: AgentResult, context: AgentContext, config
    ) -> None:
        """Challenge symptom-local candidates with likely patch owners.

        This is intentionally a compact, tool-free counterfactual pass. Owner
        paths are accepted only when they exist inside the checked-out repo, so
        the pass can improve recall without injecting hallucinated candidates.
        """
        if (
            not config.enable_comprehension_owner_challenge
            or not result.success
            or not self._has_usable_output(result.output)
        ):
            return

        current_files = _normalize_string_list(
            result.output.get("suspicious_files"),
            ("file_path", "path", "filepath", "file"),
        )
        recent_evidence = "\n".join(
            f"- {trace.get('action')}: {trace.get('result')}"
            for trace in context.agent_traces[-20:]
            if str(trace.get("action", "")).startswith("tool:")
        )
        prompt = f"""You are the counterfactual Patch-Owner Reviewer in a bug-localization system.

The first debugger often selects where the symptom executes instead of the file a minimal fix
would edit. Treat its proposed files as a hypothesis to FALSIFY, not an anchor. Independently
reconstruct the operation that produces the wrong behavior, then identify the component that:
- first consumes or normalizes the problematic value;
- performs the last incorrect mutation before the symptom;
- owns presentation of an exception when execution semantics are already correct.

Consider these alternative ownership layers:
- configuration/default normalization and registries;
- exception presentation/reporting boundaries rather than the throw site;
- protocol, lookup, field, serializer, compiler, or dispatcher that owns the invariant;
- construction/canonicalization code rather than a downstream consumer.

BUG REPORT:
{context.problem_statement[:7000]}

FIRST DEBUGGER OUTPUT:
{str(result.output)[:7000]}

RECENT CODE-EXPLORATION EVIDENCE:
{recent_evidence[:10000] or '(none)'}

REPOSITORY FILE LISTING:
{context.repo_skeleton[:50000]}

Return ONLY JSON:
{{
  "symptom_location_assessment": "why the original location is or is not the patch owner",
  "owner_files": [
    {{"path": "repository/relative/file.py", "owner_type": "configuration|presentation|protocol|dispatch|construction|other", "reason": "specific invariant owned here"}}
  ],
  "search_queries": ["exact code identifier that would reveal the owner"]
}}

Rules:
1. Propose at most {config.comprehension_owner_max_files} owner files.
   Propose at most {config.comprehension_owner_max_searches} precise search queries.
2. Do not repeat the original files unless they truly own the invariant.
3. Use only exact paths present in the repository listing.
4. Prefer a different architectural layer; do not merely list adjacent callers.
5. For exception bugs, distinguish raising/routing from rendering the final debug response.
6. For ORM bugs, distinguish query representation from the lookup/field operation that consumes it.
"""
        try:
            response = self._call_llm(
                [
                    {
                        "role": "system",
                        "content": "Audit patch ownership. Return valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                context,
                use_tools=False,
            )
        except Exception as exc:
            logger.warning(f"[ComprehensionAgent] patch-owner challenge failed: {exc}")
            return

        result.num_llm_calls += 1
        usage = getattr(response, "usage", None)
        if usage:
            result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            result.total_tokens += getattr(usage, "total_tokens", 0) or 0
        content = response.choices[0].message.content or ""
        audit = self._parse_output(content)
        queries = _normalize_string_list(audit.get("search_queries"))[
            : config.comprehension_owner_max_searches
        ]
        search_evidence = []
        if queries:
            from tools.code_search import code_search

            for query in queries:
                try:
                    matches = code_search(
                        query,
                        repo_path=context.repo_path,
                        max_results=12,
                        context_lines=1,
                        file_pattern=context.file_extension,
                    )
                except Exception as exc:
                    logger.debug(f"Owner search failed for {query!r}: {exc}")
                    continue
                result.num_tool_calls += 1
                if matches:
                    rendered = "\n".join(match.to_str() for match in matches)
                    search_evidence.append(f"QUERY {query!r}:\n{rendered}")

        # The first pass plans independent searches. A compact second pass
        # selects owners from concrete code hits, avoiding tree-only guesses.
        if search_evidence:
            evidence_prompt = f"""Select the actual patch-owner files using the retrieved code evidence.

BUG REPORT:
{context.problem_statement[:7000]}

REJECTED/SYMPTOM FILES:
{current_files}

INITIAL OWNER AUDIT:
{audit}

CODE SEARCH EVIDENCE:
{chr(10).join(search_evidence)[:30000]}

Return ONLY JSON with the same owner_files schema. Choose at most
{config.comprehension_owner_max_files} repository-relative paths. Prefer the file whose shown
function consumes/normalizes the value or renders the final response, not a nearby caller.
"""
            try:
                evidence_response = self._call_llm(
                    [
                        {
                            "role": "system",
                            "content": "Select patch owners from concrete evidence. JSON only.",
                        },
                        {"role": "user", "content": evidence_prompt},
                    ],
                    context,
                    use_tools=False,
                )
                result.num_llm_calls += 1
                evidence_usage = getattr(evidence_response, "usage", None)
                if evidence_usage:
                    result.prompt_tokens += (
                        getattr(evidence_usage, "prompt_tokens", 0) or 0
                    )
                    result.completion_tokens += (
                        getattr(evidence_usage, "completion_tokens", 0) or 0
                    )
                    result.total_tokens += getattr(evidence_usage, "total_tokens", 0) or 0
                evidence_content = evidence_response.choices[0].message.content or ""
                evidence_audit = self._parse_output(evidence_content)
                if evidence_audit.get("owner_files"):
                    audit["initial_owner_files"] = audit.get("owner_files", [])
                    audit["owner_files"] = evidence_audit["owner_files"]
                    audit["search_evidence"] = search_evidence
            except Exception as exc:
                logger.warning(
                    f"[ComprehensionAgent] owner evidence synthesis failed: {exc}"
                )

        raw_owners = audit.get("owner_files") or []
        if not isinstance(raw_owners, list):
            raw_owners = [raw_owners]

        owners: list[str] = []
        root = Path(context.repo_path).resolve()
        for entry in raw_owners:
            path = _normalize_suspicious_file_entry(entry, context.repo_path)
            if not path or path in current_files or path in owners:
                continue
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                owners.append(path)
            if len(owners) >= config.comprehension_owner_max_files:
                break

        result.output["patch_owner_audit"] = audit
        if owners:
            result.output["suspicious_files"] = owners + current_files
            result.output["patch_owner_files"] = owners
            context.add_trace(self.name, "patch_owner_challenge", content[:300])

    def get_system_prompt(self, context: AgentContext) -> str:
        from config import config

        prompt = SYSTEM_PROMPT
        if config.hypotheses_enabled:
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
                    p = _normalize_suspicious_file_entry(e, context.repo_path)
                    if p and p not in files:
                        files.append(p)
            elif isinstance(raw, (str, dict)):
                p = _normalize_suspicious_file_entry(raw, context.repo_path)
                if p:
                    files = [p]
            context.candidate_files = files

        if "suspicious_functions" in output:
            context.candidate_methods = _normalize_string_list(
                output["suspicious_functions"],
                ("function_name", "method_name", "name", "function", "method"),
            )

        if "search_keywords" in output:
            for keyword in _normalize_string_list(
                output.get("search_keywords"), ("keyword", "term", "query")
            ):
                if keyword not in context.keywords:
                    context.keywords.append(keyword)

        self._process_hypotheses(output, context)

        # Hypothesis-loop merging ranks high-prior symptom hypotheses first.
        # Preserve the counterfactual audit's contract: validated patch owners
        # must remain ahead of symptom-local candidates after all merges.
        owner_files = _normalize_string_list(output.get("patch_owner_files"))
        if owner_files:
            context.candidate_files = owner_files + [
                path for path in context.candidate_files if path not in owner_files
            ]

        logger.info(
            f"[ComprehensionAgent] Hypothesis: {context.fault_hypothesis[:200]}"
        )
        logger.info(f"[ComprehensionAgent] Candidate files: {context.candidate_files}")

    @staticmethod
    def _process_hypotheses(output: dict, context: AgentContext):
        """E1: build the hypothesis tracker from comprehension output."""
        from config import config

        if not config.hypotheses_enabled:
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

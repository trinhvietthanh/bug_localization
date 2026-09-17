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


SINGLE_SHOT_SYSTEM_PROMPT = """You are a Fault Confirmation Agent — a meticulous code reviewer and debugger.
Previous agents explored the codebase and collected EVIDENCE for each candidate location:
actual source excerpts plus an explorer relevance score and reasoning. Your task is to rank
these candidates by likelihood of containing the bug, judging the provided source directly.

CRITICAL RANKING RULES for Top-1 accuracy:
- Files mentioned in stack traces or error messages should be STRONGLY preferred for rank 1
- The rank 1 location must be the file a FIX PATCH would edit — behavior implemented in one
  layer is often fixed in another (settings/defaults, serializer, base class, SQL compiler)
- Assign HIGH confidence (0.8+) ONLY when the excerpt shows a clear bug that directly explains
  the reported behavior
- Assign MEDIUM confidence (0.5-0.79) when the code is suspicious but the bug pattern is not obvious
- DO NOT give high confidence to test files unless the bug is clearly in test setup/assertion logic

Respond with your FINAL ranked results as a JSON block:

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
      "explanation": "Why this is the buggy location and what the bug is"
    }
  ],
  "overall_analysis": "Summary of your debugging analysis",
  "root_cause": "Your best assessment of the root cause"
}
```

Rank ALL candidates that are plausibly related (aim for 8-10 entries). Use the line numbers
shown in the excerpts for start_line/end_line. Tools are NOT available — judge from the
evidence provided.
"""


VERIFY_SYSTEM_PROMPT = """You are a Fault Confirmation Agent making the FINAL rank-1 decision.
A previous pass already ranked candidates; your ONLY job is to decide which of the few
candidates below is the file a FIX PATCH would actually edit.

Beware the symptom/fix split: behavior implemented in one layer is often fixed in another —
a settings/defaults module, a serializer, a base class, a SQL compiler, a config registry.
If unsure, use the tools (read_file, code_search, find_callers, find_callees) to read the
decisive context — but be surgical, you have very few iterations.

Respond with a JSON block ranking ONLY the given candidates:

```json
{
  "ranked_locations": [
    {"rank": 1, "file_path": "path/to/file.py", "function_name": "f",
     "class_name": "C", "start_line": 0, "end_line": 0,
     "confidence": 0.9, "explanation": "why the patch edits THIS file"}
  ],
  "root_cause": "one-sentence root cause"
}
```
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

    def run(self, context: AgentContext, max_iterations: int = None) -> AgentResult:
        """Evidence-grounded single shot with a structural fallback trigger.

        The explorer already read and scored the candidates; when its evidence
        is rich enough, one listwise call replaces the ~10-iteration re-reading
        loop. Escalation is structural only (thin evidence / free-form
        navigation) — self-reported confidence is uninformative.

        Modes (config.confirmation_mode):
          loop   — full tool loop (default; best Top-1)
          single — listwise shot only (best Top-3/5, weak Top-1)
          hybrid — listwise shot, then a short verify loop that only
                   discriminates rank 1 among the shot's top-3; the tail of
                   the listwise ranking is preserved (recall intact)
        """
        from config import config

        mode = getattr(config, "confirmation_mode", "loop")
        if mode not in ("single", "hybrid"):
            return super().run(context, max_iterations)

        evidence = self._build_evidence_pack(context)
        with_excerpts = sum(1 for e in evidence if e.get("excerpt"))
        top_score = max(
            ((e.get("suspicion_score") or 0) for e in evidence), default=0
        )
        # Sufficient evidence: either breadth (several excerpted candidates)
        # or confident convergence — the explorer scored a location ≥ 0.8
        # (its own early-success bar), so a concentrated pack is a signal,
        # not a gap
        sufficient = with_excerpts >= config.confirmation_min_evidence or (
            with_excerpts >= 1 and top_score >= 0.8
        )
        if context.navigation_was_freeform or not sufficient:
            logger.info(
                "[ConfirmationAgent] structural fallback to tool loop "
                f"(freeform={context.navigation_was_freeform}, "
                f"excerpts={with_excerpts}, top_score={top_score})"
            )
            return super().run(context, max_iterations)

        shot = self._run_single_shot(context, evidence)
        if shot is not None and (shot.output or {}).get("ranked_locations"):
            if mode == "hybrid":
                self._verify_top(shot, context)
            return shot

        logger.info("[ConfirmationAgent] single shot failed — tool loop")
        loop_result = super().run(context, max_iterations)
        if shot is not None:
            loop_result.num_llm_calls += shot.num_llm_calls
            loop_result.prompt_tokens += shot.prompt_tokens
            loop_result.completion_tokens += shot.completion_tokens
            loop_result.total_tokens += shot.total_tokens
        return loop_result

    def _build_evidence_pack(self, context: AgentContext) -> list[dict]:
        """Merge explorer locations with the candidate pool; attach excerpts.
        Pure Python — zero LLM calls."""
        from pathlib import Path

        from config import config

        entries: list[dict] = []
        seen_files: set[str] = set()

        def _is_real_file(fp: str) -> bool:
            # run_search observations carry the QUERY STRING as file_path —
            # only paths that exist on disk may consume evidence slots
            try:
                return (Path(context.repo_path) / fp).is_file()
            except OSError:
                return False

        locs = sorted(
            (l for l in context.suspicious_locations if l.get("file_path")),
            key=lambda l: l.get("suspicion_score", 0) or 0,
            reverse=True,
        )
        for loc in locs:
            fp = loc["file_path"]
            if fp in seen_files or not _is_real_file(fp):
                continue
            seen_files.add(fp)
            entries.append(dict(loc))
            if len(entries) >= config.confirmation_evidence_top_k:
                break

        # Backfill with candidate files the explorer did not score
        for fp in context.candidate_files:
            if len(entries) >= config.confirmation_evidence_top_k:
                break
            if fp and fp not in seen_files and _is_real_file(fp):
                seen_files.add(fp)
                entries.append({"file_path": fp})

        for e in entries:
            e["markers"] = self._markers(e["file_path"], context)
            e["excerpt"] = self._excerpt(
                context, e, config.confirmation_evidence_max_lines
            )
        return entries

    @staticmethod
    def _markers(file_path: str, context: AgentContext) -> str:
        markers = []
        if file_path in context.stack_trace_files:
            markers.append("[IN STACK TRACE]")
        if file_path in context.mentioned_files:
            markers.append("[MENTIONED IN REPORT]")
        return " ".join(markers)

    @staticmethod
    def _excerpt(context: AgentContext, loc: dict, max_lines: int) -> str | None:
        """Source excerpt with line numbers: explorer line range, else
        function source via AST, else file head."""
        from pathlib import Path
        from tools.cache import read_file_cached

        fp = loc.get("file_path", "")
        full = Path(context.repo_path) / fp
        try:
            text = read_file_cached(str(full))
        except OSError:
            return None
        lines = text.split("\n")

        start = int(loc.get("start_line") or 0)
        end = int(loc.get("end_line") or 0)
        if 0 < start <= end <= len(lines):
            lo = max(1, start - 5)
            hi = min(len(lines), max(end + 5, lo + 9))
        elif loc.get("function_name"):
            from tools.ast_parser import get_function_source

            src = get_function_source(
                fp, context.repo_path,
                loc["function_name"], loc.get("class_name") or None,
            )
            if src and not src.startswith("Function '"):
                cut = src.split("\n")
                if len(cut) > max_lines:
                    cut = cut[:max_lines] + ["   ... (truncated)"]
                return "\n".join(cut)
            lo, hi = 1, min(len(lines), 40)
        else:
            lo, hi = 1, min(len(lines), 40)

        if hi - lo + 1 > max_lines:
            hi = lo + max_lines - 1
        numbered = [f"{i:4d} | {lines[i - 1]}" for i in range(lo, hi + 1)]
        return "\n".join(numbered)

    def _run_single_shot(
        self, context: AgentContext, evidence: list[dict]
    ) -> AgentResult | None:
        """One tool-free listwise call over the evidence pack."""
        result = AgentResult(agent_name=self.name)

        parts = [
            "## Bug Report\n",
            context.problem_statement[:2000],
            "",
            "## Fault Hypothesis\n",
            context.fault_hypothesis or "No hypothesis available.",
            "",
        ]
        if context.error_messages:
            parts.append("## Error Messages\n")
            parts.extend(f"- {e}" for e in context.error_messages[:5])
            parts.append("")
        if context.stack_trace_files:
            parts.append("## Stack Trace Files (HIGHEST PRIORITY for rank 1)\n")
            parts.extend(f"- {f}" for f in context.stack_trace_files[:10])
            parts.append("")

        parts.append("## Candidate Evidence (explorer-scored, with source)\n")
        for i, e in enumerate(evidence, 1):
            entity = e["file_path"]
            if e.get("function_name"):
                cls = e.get("class_name") or ""
                entity += " :: " + (f"{cls}." if cls else "") + e["function_name"]
            header = f"### Candidate {i}: {entity}"
            if e.get("markers"):
                header += f"  {e['markers']}"
            parts.append(header)
            score = e.get("suspicion_score")
            if score is not None or e.get("reason"):
                parts.append(
                    f"Explorer: relevance={score if score is not None else '?'}"
                    + (f" — {e['reason']}" if e.get("reason") else "")
                )
            if e.get("excerpt"):
                parts.append("```\n" + e["excerpt"] + "\n```")
            else:
                parts.append("(source unavailable)")
            parts.append("")

        parts.append(
            "Rank these candidates now. Remember: rank 1 = the file a fix patch "
            "would edit. Respond with the required JSON block."
        )

        messages = [
            {"role": "system", "content": SINGLE_SHOT_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]
        try:
            response = self._call_llm(messages, context, use_tools=False)
        except Exception as e:
            logger.warning(f"[ConfirmationAgent] single-shot call failed: {e}")
            return None
        result.num_llm_calls = 1
        usage = getattr(response, "usage", None)
        if usage:
            result.prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            result.total_tokens = getattr(usage, "total_tokens", 0) or 0
        content = response.choices[0].message.content or ""
        if not content.strip():
            return None
        result.output = self._parse_output(content)
        result.explanation = content
        result.success = True
        context.add_trace(self.name, "confirmation_single_shot", content[:300])
        return result

    def _verify_top(self, shot: AgentResult, context: AgentContext) -> None:
        """Hybrid stage 2: short tool loop deciding rank 1 among the shot's
        top-3 files; the listwise tail is preserved. Mutates `shot` in place;
        any failure keeps the listwise ranking untouched."""
        from config import config

        locs = shot.output.get("ranked_locations") or []
        top: list[dict] = []
        seen: set[str] = set()
        for loc in locs:
            fp = loc.get("file_path", "")
            if fp and fp not in seen:
                seen.add(fp)
                top.append(loc)
            if len(top) >= 3:
                break
        if len(top) < 2:
            return  # nothing to discriminate

        # The verify payload travels on the per-instance context because the
        # agent object is shared across benchmark worker threads
        context._verify_stage_message = self._build_verify_message(context, top)
        try:
            verify = super().run(
                context, max_iterations=config.confirmation_verify_iters
            )
        finally:
            context._verify_stage_message = None

        shot.num_llm_calls += verify.num_llm_calls
        shot.num_tool_calls += verify.num_tool_calls
        shot.prompt_tokens += verify.prompt_tokens
        shot.completion_tokens += verify.completion_tokens
        shot.total_tokens += verify.total_tokens

        v_locs = (verify.output or {}).get("ranked_locations") if verify.success else None
        if not v_locs:
            logger.info("[ConfirmationAgent] verify stage empty — keeping listwise order")
            return
        allowed = {l.get("file_path", "") for l in top}
        by_fp = {l.get("file_path", ""): l for l in top}
        head: list[dict] = []
        head_seen: set[str] = set()
        for loc in v_locs:
            fp = loc.get("file_path", "")
            # Only the given candidates may be reordered — verify cannot
            # inject new files (recall tail must stay intact)
            if fp in allowed and fp not in head_seen:
                head_seen.add(fp)
                merged = dict(by_fp[fp])
                for key in ("confidence", "explanation", "function_name",
                            "class_name", "start_line", "end_line"):
                    if loc.get(key):
                        merged[key] = loc[key]
                head.append(merged)
        for loc in top:  # candidates verify dropped keep their relative order
            if loc.get("file_path", "") not in head_seen:
                head.append(loc)

        tail = locs[len(top):]
        final = head + tail
        for rank, loc in enumerate(final, 1):
            loc["rank"] = rank
        shot.output["ranked_locations"] = final
        if (verify.output or {}).get("root_cause"):
            shot.output["root_cause"] = verify.output["root_cause"]
        changed = bool(
            head and head[0].get("file_path") != top[0].get("file_path")
        )
        logger.info(
            "[ConfirmationAgent] hybrid verify: top-1 "
            + (f"changed to {head[0].get('file_path', '?')}" if changed else "confirmed")
        )
        context.add_trace(
            self.name, "hybrid_verify",
            "changed" if changed else "confirmed",
        )

    def _build_verify_message(self, context: AgentContext, top: list[dict]) -> str:
        from config import config

        parts = [
            "## Bug Report\n",
            context.problem_statement[:1500],
            "",
            "## Fault Hypothesis\n",
            context.fault_hypothesis or "No hypothesis available.",
            "",
            "## Finalist Candidates (decide rank 1 among THESE ONLY)\n",
        ]
        for i, loc in enumerate(top, 1):
            fp = loc.get("file_path", "")
            entity = fp
            if loc.get("function_name"):
                cls = loc.get("class_name") or ""
                entity += " :: " + (f"{cls}." if cls else "") + loc["function_name"]
            parts.append(f"### Finalist {i}: {entity}")
            if loc.get("explanation"):
                parts.append(f"Previous verdict: {str(loc['explanation'])[:300]}")
            excerpt = self._excerpt(
                context, loc, config.confirmation_evidence_max_lines * 2
            )
            parts.append("```\n" + excerpt + "\n```" if excerpt else "(source unavailable)")
            parts.append("")
        parts.append(
            "Which finalist is the file the fix patch would edit? Read more "
            "context with the tools ONLY if the excerpts are not decisive. "
            "Then respond with the required JSON block ranking the finalists."
        )
        return "\n".join(parts)

    def get_system_prompt(self, context: AgentContext) -> str:
        if getattr(context, "_verify_stage_message", None):
            return VERIFY_SYSTEM_PROMPT
        return SYSTEM_PROMPT

    def get_initial_message(self, context: AgentContext) -> str:
        verify_message = getattr(context, "_verify_stage_message", None)
        if verify_message:
            return verify_message
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

        # Update context with final results. Confirmed files go first, but keep
        # Navigation's broader candidate list behind them — dropping it capped
        # the final ranked list at 1-3 files and starved Top-5 recall.
        if ranked:
            confirmed = [
                loc["file_path"] for loc in ranked if loc.get("file_path")
            ]
            prior = [f for f in context.candidate_files if f not in confirmed]
            context.candidate_files = confirmed + prior
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

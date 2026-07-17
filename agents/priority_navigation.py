"""
E2: Priority-guided Navigation Agent (OrcaLoca-style).

Replaces the free-form tool-calling loop of NavigationAgent with a
deterministic priority-queue explorer (core.explorer). Each step is one
registry-tool execution plus one small tool-free "observe" LLM call with
O(1) context — no message history is carried between calls.

The output schema (``suspicious_locations``) is identical to
NavigationAgent's, so ``process_result``, Confirmation, the candidate pool,
and unified scoring need zero changes. Final output is assembled in pure
Python, which makes empty-prediction/JSON-parse failures structurally
impossible in this phase.
"""

from __future__ import annotations

import logging
import re

from agents.base_agent import AgentContext, AgentResult
from agents.navigation import NavigationAgent
from core.explorer import (
    PRIOR_HYPOTHESIS,
    PRIOR_MENTIONED,
    PRIOR_PATH_KEYWORD,
    PRIOR_STACK_TRACE,
    ExplorationAction,
    Observation,
    PriorityExplorer,
)
from config import config

logger = logging.getLogger(__name__)

OBSERVE_SYSTEM_PROMPT = """You are scoring ONE code entity for its likelihood of containing or \
directly propagating a specific bug. Do not re-score previous findings; judge only the entity \
shown. Respond with JSON only:
{"relevance": 0, "reason": "<25 words", "is_likely_fault_location": false,
 "start_line": 0, "end_line": 0,
 "new_entities": [{"target": "file.py or file.py::func", "kind": "inspect_file|inspect_function|expand_callers|expand_callees", "why": "<10 words"}],
 "new_queries": ["search term worth running"],
 "hypothesis_evidence": [{"hid": "HYP1", "direction": 1, "strength": "weak|moderate|strong", "note": "<20 words"}]}
relevance: 0-10 (10 = this is almost certainly the fault location).
new_entities/new_queries: only genuinely promising leads (0-3 each).
hypothesis_evidence: only if the observation clearly bears on a listed hypothesis."""

DECOMPOSE_SYSTEM_PROMPT = """Decompose this bug report into 2-4 focused code-search sub-queries \
(identifiers, error fragments, distinctive phrases). Respond with JSON only:
{"sub_queries": ["...", "..."]}"""


class PriorityNavigationAgent(NavigationAgent):
    """Navigation via scored exploration frontier instead of free-form looping."""

    def run(self, context: AgentContext, max_iterations: int = None) -> AgentResult:
        self._register_language_tools(context.language)
        result = AgentResult(agent_name=self.name)

        try:
            explorer = self._build_explorer(result, context)
            self._seed_explorer(explorer, result, context)
            findings = explorer.run()
            # Persist visited set so reflection rounds don't redo work
            context.exploration_visited = explorer.frontier.visited

            locations = self._findings_to_locations(findings)
            if (
                len([f for f in findings if f.relevance >= 5]) < 3
                and config.exploration_fallback_to_freeform
            ):
                logger.info(
                    "[PriorityNavigation] thin findings "
                    f"({len(findings)}, stop={explorer.stop_reason}) — "
                    "falling back to free-form navigation"
                )
                fallback = super().run(context, max_iterations)
                # Merge: keep explorer findings behind the free-form ones
                fb_locs = (fallback.output or {}).get("suspicious_locations", [])
                seen = {l.get("file_path", "") + "::" + str(l.get("function_name", "")) for l in fb_locs}
                for loc in locations:
                    key = loc.get("file_path", "") + "::" + str(loc.get("function_name", ""))
                    if key not in seen:
                        fb_locs.append(loc)
                fallback.output["suspicious_locations"] = fb_locs
                fallback.num_llm_calls += result.num_llm_calls
                fallback.num_tool_calls += result.num_tool_calls
                fallback.prompt_tokens += result.prompt_tokens
                fallback.completion_tokens += result.completion_tokens
                fallback.total_tokens += result.total_tokens
                context.suspicious_locations = fb_locs
                context.navigation_was_freeform = True
                return fallback

            context.suspicious_locations = locations
            context.navigation_was_freeform = False
            result.output = {
                "suspicious_locations": locations,
                "investigation_summary": (
                    f"Priority exploration: {explorer.actions_executed} actions, "
                    f"stop={explorer.stop_reason}, {len(findings)} findings "
                    f"(relevance ≥ 6)."
                ),
            }
            result.explanation = result.output["investigation_summary"]
            result.success = True
            context.add_trace(self.name, "priority_exploration", result.explanation)
            return result
        except Exception as e:
            logger.warning(
                f"[PriorityNavigation] explorer crashed ({e}) — "
                "falling back to free-form navigation"
            )
            context.navigation_was_freeform = True
            fallback = super().run(context, max_iterations)
            context.suspicious_locations = (
                (fallback.output or {}).get("suspicious_locations") or []
            )
            fallback.num_llm_calls += result.num_llm_calls
            fallback.num_tool_calls += result.num_tool_calls
            fallback.prompt_tokens += result.prompt_tokens
            fallback.completion_tokens += result.completion_tokens
            fallback.total_tokens += result.total_tokens
            return fallback

    # ── explorer wiring ──────────────────────────────────────────────────────

    def _build_explorer(
        self, result: AgentResult, context: AgentContext
    ) -> PriorityExplorer:
        w_graph = (
            config.exploration_w_graph_java
            if context.language == "java"
            else config.exploration_w_graph
        )

        anchor_files = list(
            dict.fromkeys(
                (context.stack_trace_files or [])
                + (context.mentioned_files or [])
                + [
                    fp
                    for h in (context.hypotheses or [])
                    for fp in h.suspected_files
                ]
            )
        )
        graph_distances: dict[str, int] = {}
        gr = context.graph_retriever
        if gr is not None:
            try:
                # Hypothesis-text anchors widen coverage beyond known files
                if context.fault_hypothesis and hasattr(gr, "find_anchor_nodes"):
                    for node, _score in gr.find_anchor_nodes(
                        context.fault_hypothesis, top_k=5
                    ):
                        if node.file_path and node.file_path not in anchor_files:
                            anchor_files.append(node.file_path)
                graph_distances = gr.file_hop_distances(
                    anchor_files, max_hops=config.exploration_max_depth
                )
            except Exception as e:
                logger.debug(f"[PriorityNavigation] graph distances unavailable: {e}")

        static_priors: dict[str, float] = {}
        for fp in context.stack_trace_files or []:
            static_priors[fp] = PRIOR_STACK_TRACE
        for fp in (context.mentioned_files or []) + (context.test_derived_candidates or []):
            static_priors.setdefault(fp, PRIOR_MENTIONED)
        tracker = context.hypothesis_tracker
        if tracker is not None:
            for h in tracker.surviving():
                for fp in h.suspected_files:
                    static_priors.setdefault(fp, PRIOR_HYPOTHESIS * h.posterior)
        else:
            for h in context.hypotheses or []:
                for fp in h.suspected_files:
                    static_priors.setdefault(fp, PRIOR_HYPOTHESIS * h.prior)
        for fp in context.candidate_files or []:
            static_priors.setdefault(fp, PRIOR_PATH_KEYWORD)

        explorer = PriorityExplorer(
            execute=lambda action: self._execute_action(action, result, context),
            observe=lambda action, out: self._observe_action(
                action, out, result, context
            ),
            max_actions=config.exploration_max_actions,
            min_priority=config.exploration_min_priority,
            w_llm=config.exploration_w_llm,
            w_graph=w_graph,
            w_signal=config.exploration_w_signal,
            max_depth=config.exploration_max_depth,
            graph_distances=graph_distances,
            static_priors=static_priors,
        )
        # Reflection rounds: don't redo already-visited actions
        prior_visited = getattr(context, "exploration_visited", None)
        if prior_visited:
            explorer.frontier.visited |= set(prior_visited)
        return explorer

    def _seed_explorer(
        self, explorer: PriorityExplorer, result: AgentResult, context: AgentContext
    ) -> None:
        for fp in context.stack_trace_files or []:
            explorer.seed("inspect_file", fp, origin="stack_trace")
        for fp in context.test_derived_candidates or []:
            explorer.seed("inspect_file", fp, origin="test_derived")
        for fp in (context.mentioned_files or [])[:5]:
            explorer.seed("inspect_file", fp, origin="mentioned")
        for fp in (context.candidate_files or [])[:8]:
            explorer.seed("inspect_file", fp, origin="comprehension")

        # E1 probes become high-priority frontier actions
        for h in context.hypotheses or []:
            for fp in h.suspected_files[:3]:
                explorer.seed("inspect_file", fp, origin=h.hid)
            for probe in h.probes[:2]:
                target = self._probe_to_query(probe)
                if target:
                    explorer.seed("run_search", target, origin=f"{h.hid}:probe")

        for q in self._sub_queries(result, context):
            explorer.seed("run_search", q, origin="decomposition")

    @staticmethod
    def _probe_to_query(probe) -> str:
        hint = (probe.query_hint or "").strip()
        m = re.search(r'\(\s*["\']?([^"\')]+)', hint)
        if m:
            return m.group(1).strip()
        return (probe.description or "")[:60]

    def _sub_queries(self, result: AgentResult, context: AgentContext) -> list[str]:
        """Hypotheses are already a decomposition; otherwise one LLM call."""
        if context.hypotheses:
            return [
                (h.suspected_component or h.statement)[:80]
                for h in context.hypotheses[:4]
            ]
        user = (
            f"Bug report:\n{context.problem_statement[:2000]}\n\n"
            f"Fault hypothesis: {context.fault_hypothesis[:400]}"
        )
        try:
            response = self._call_llm(
                [
                    {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                context,
                use_tools=False,
            )
            self._track_usage(result, response)
            parsed = self._parse_output(response.choices[0].message.content or "")
            queries = [
                str(q).strip() for q in (parsed.get("sub_queries") or []) if str(q).strip()
            ]
            return queries[:4]
        except Exception as e:
            logger.debug(f"[PriorityNavigation] decomposition failed: {e}")
            return [kw for kw in (context.keywords or [])[:3]]

    # ── action execution + observation ───────────────────────────────────────

    def _execute_action(
        self, action: ExplorationAction, result: AgentResult, context: AgentContext
    ) -> str:
        kind, target = action.kind, action.target
        file_part, _, entity_part = target.partition("::")
        result.num_tool_calls += 1

        if kind == "inspect_file":
            if context.language == "python" and "get_file_outline" in self.tools:
                out = self._execute_tool("get_file_outline", {"file_path": target}, context)
                # Outline plus the file head gives the observer real code to judge
                head = self._execute_tool(
                    "read_file", {"file_path": target, "start_line": 1, "end_line": 80},
                    context,
                )
                return f"{out}\n\n--- file head ---\n{head}"
            return self._execute_tool(
                "read_file", {"file_path": target, "start_line": 1, "end_line": 120},
                context,
            )
        if kind == "inspect_function":
            name = entity_part or target
            class_name = None
            if "." in name:
                class_name, name = name.rsplit(".", 1)
            args = {"function_name": name}
            if file_part and entity_part:
                args["file_path"] = file_part
            if class_name:
                args["class_name"] = class_name
            return self._execute_tool("get_function_source", args, context)
        if kind == "expand_callers":
            return self._execute_tool(
                "find_callers", {"function_name": (entity_part or target).rsplit(".", 1)[-1]},
                context,
            )
        if kind == "expand_callees":
            return self._execute_tool(
                "find_callees", {"function_name": (entity_part or target).rsplit(".", 1)[-1]},
                context,
            )
        if kind == "run_search":
            out = self._execute_tool("code_search", {"query": target}, context)
            if context.retriever is not None and "semantic_file_search" in self.tools:
                sem = self._execute_tool(
                    "semantic_file_search", {"query": target, "top_k": 5}, context
                )
                out = f"{out}\n\n--- semantic file search ---\n{sem}"
            return out
        return ""

    def _observe_action(
        self,
        action: ExplorationAction,
        tool_output: str,
        result: AgentResult,
        context: AgentContext,
    ) -> Observation | None:
        fp = action.target.split("::", 1)[0]
        bug_line = (
            context.structured_bug_info.get("bug_phenomenon", "")
            if context.structured_bug_info
            else ""
        ) or context.problem_statement[:300]

        top_findings = ""  # names + scores only, O(1) context
        # (populated from prior observations via the trace to stay stateless)
        recent = [
            t for t in context.agent_traces
            if t.get("agent") == self.name and t.get("action") == "finding"
        ][-8:]
        if recent:
            top_findings = "\n".join(f"- {t['result']}" for t in recent)

        hyp_block = ""
        if context.hypotheses:
            hyp_block = "Hypotheses under test:\n" + "\n".join(
                f"- {h.hid}: {h.statement[:120]}" for h in context.hypotheses[:5]
            )

        user = "\n".join(filter(None, [
            f"Bug (summary): {bug_line}",
            f"Fault hypothesis: {(context.fault_hypothesis or '')[:400]}",
            hyp_block,
            f"Currently inspecting: {action.target} "
            f"(kind={action.kind}, spawned by {action.origin})",
            f"--- tool output ---\n{tool_output[:6000]}",
            f"--- current top findings (do not re-score) ---\n{top_findings}"
            if top_findings else "",
            "Score this entity now.",
        ]))

        try:
            response = self._call_llm(
                [
                    {"role": "system", "content": OBSERVE_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                context,
                use_tools=False,
            )
            self._track_usage(result, response)
        except Exception as e:
            logger.debug(f"[PriorityNavigation] observe call failed: {e}")
            return None

        parsed = self._parse_output(response.choices[0].message.content or "")
        if not parsed or "raw_response" in parsed and len(parsed) == 1:
            return None

        try:
            relevance = int(parsed.get("relevance", 0))
        except (TypeError, ValueError):
            relevance = 0
        relevance = max(0, min(relevance, 10))

        def _int(key):
            try:
                return int(parsed.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        obs = Observation(
            entity=action.target,
            kind=action.kind,
            relevance=relevance,
            reason=str(parsed.get("reason", ""))[:200],
            is_likely_fault_location=bool(parsed.get("is_likely_fault_location")),
            file_path=fp,
            start_line=_int("start_line"),
            end_line=_int("end_line"),
            new_entities=parsed.get("new_entities") or [],
            new_queries=parsed.get("new_queries") or [],
        )

        if relevance >= 6:
            context.add_trace(
                self.name, "finding", f"{action.target} (relevance {relevance}/10)"
            )

        # E1 feedback: observations double as verification evidence
        tracker = context.hypothesis_tracker
        if tracker is not None:
            from agents.hypothesis import EvidenceItem

            for ev in parsed.get("hypothesis_evidence") or []:
                if not isinstance(ev, dict):
                    continue
                try:
                    direction = int(ev.get("direction", 0))
                except (TypeError, ValueError):
                    direction = 0
                strength = str(ev.get("strength", "moderate")).lower()
                if strength not in ("weak", "moderate", "strong"):
                    strength = "moderate"
                tracker.update(str(ev.get("hid", "")).strip(), EvidenceItem(
                    probe_description=f"explorer:{action.target}",
                    direction=direction,
                    strength=strength,
                    source_tool=action.kind,
                    location=action.target,
                    note=str(ev.get("note", ""))[:120],
                ))
        return obs

    @staticmethod
    def _findings_to_locations(findings) -> list[dict]:
        locations = []
        for obs in findings:
            # run_search observations carry the QUERY STRING as file_path
            # (action.target is the query) — they are intermediate steps whose
            # real files were already spawned via new_entities. Emitting them
            # pollutes candidate_files with grep strings downstream.
            if obs.kind == "run_search":
                continue
            _, _, entity_part = obs.entity.partition("::")
            function_name = ""
            class_name = ""
            if entity_part:
                if "." in entity_part:
                    class_name, function_name = entity_part.rsplit(".", 1)
                else:
                    function_name = entity_part
            locations.append({
                "file_path": obs.file_path,
                "function_name": function_name,
                "class_name": class_name,
                "start_line": obs.start_line,
                "end_line": obs.end_line,
                "suspicion_score": round(obs.relevance / 10.0, 2),
                "reason": obs.reason,
            })
        return locations

    @staticmethod
    def _track_usage(result: AgentResult, response) -> None:
        result.num_llm_calls += 1
        usage = getattr(response, "usage", None)
        if usage:
            result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            result.total_tokens += getattr(usage, "total_tokens", 0) or 0

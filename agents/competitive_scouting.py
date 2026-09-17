"""
MACS: Multi-Agent Competitive Scouting.

Splits the bug report into 2-3 competing hypotheses (reusing E1's Comprehension
output) and runs one Explorer per hypothesis **in parallel** over a shared
blackboard (``SharedScoutBoard``). Each scout is a ``PriorityExplorer`` (the E2
engine) with its own ``SharedFrontier``; the frontiers are independent queues
but consult the same board, so the instant one scout falsifies a hypothesis (or
a file keeps scoring low) that cluster is dropped from every scout's frontier.

Design reuse:
  * The exploration engine, action execution, observation scoring and
    finding→location assembly are all inherited from ``PriorityNavigationAgent``.
  * Belief math stays in the existing ``HypothesisTracker``; the board only adds
    a lock around it plus the prune bookkeeping.
  * Output schema is unchanged (``suspicious_locations``), so Confirmation, the
    candidate pool and unified scoring need no changes.

Falls back to single-threaded ``PriorityNavigationAgent`` behaviour when there
are no hypotheses to split (E1 produced none) or when the explorers find too
little (``exploration_fallback_to_freeform``).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.base_agent import AgentContext, AgentResult
from agents.priority_navigation import PriorityNavigationAgent
from agents.scout_board import SharedScoutBoard
from core.explorer import PriorityExplorer, SharedFrontier
from config import config

logger = logging.getLogger(__name__)


class CompetitiveScoutingAgent(PriorityNavigationAgent):
    """Navigation via parallel per-hypothesis scouts over a shared blackboard."""

    def run(self, context: AgentContext, max_iterations: int = None) -> AgentResult:
        self._register_language_tools(context.language)

        hypotheses = context.hypotheses or []
        if len(hypotheses) < 2:
            # Nothing to compete — fall back to the single explorer (E2).
            logger.info(
                "[MACS] fewer than 2 hypotheses "
                f"({len(hypotheses)}) — using single-explorer navigation"
            )
            return super().run(context, max_iterations)

        result = AgentResult(agent_name=self.name)
        try:
            return self._run_scouts(hypotheses, result, context, max_iterations)
        except Exception as e:
            logger.warning(
                f"[MACS] competitive scouting crashed ({e}) — "
                "falling back to free-form navigation"
            )
            context.navigation_was_freeform = True
            fallback = super(PriorityNavigationAgent, self).run(context, max_iterations)
            context.suspicious_locations = (
                (fallback.output or {}).get("suspicious_locations") or []
            )
            self._merge_stats(result, fallback)
            return fallback

    # ── scout orchestration ──────────────────────────────────────────────────

    def _run_scouts(
        self,
        hypotheses: list,
        result: AgentResult,
        context: AgentContext,
        max_iterations: int | None,
    ) -> AgentResult:
        # Compute shared, read-only priors/distances once on the main thread.
        # This also pre-warms the GraphRetriever's lazy indexes/caches before
        # the scouts fan out (those caches are not thread-safe).
        w_graph = self._w_graph_for(context)
        graph_distances = self._compute_graph_distances(context)
        static_priors = self._compute_static_priors(context)

        board = SharedScoutBoard(context.hypothesis_tracker, hypotheses)

        # Pick the top-N hypotheses by prior; one scout each.
        n = max(2, min(config.scouting_num_scouts, len(hypotheses)))
        scouts = sorted(hypotheses, key=lambda h: h.prior, reverse=True)[:n]

        prior_visited = set(getattr(context, "exploration_visited", None) or set())

        def run_one(h) -> tuple[list, AgentResult, set]:
            scout_result = AgentResult(agent_name=f"{self.name}:{h.hid}")
            frontier = SharedFrontier(board, scout_id=h.hid)
            frontier.visited |= prior_visited

            def observe(action, out):
                return self._observe_action(
                    action, out, scout_result, context,
                    evidence_handler=lambda ctx, parsed, act, obs: self._board_evidence(
                        board, ctx, parsed, act, obs
                    ),
                )

            explorer = PriorityExplorer(
                execute=lambda action: self._scout_execute(
                    action, scout_result, context, board
                ),
                observe=observe,
                max_actions=config.exploration_max_actions,
                min_priority=config.exploration_min_priority,
                w_llm=config.exploration_w_llm,
                w_graph=w_graph,
                w_signal=config.exploration_w_signal,
                max_depth=config.exploration_max_depth,
                graph_distances=graph_distances,
                static_priors=static_priors,
                frontier=frontier,
            )
            self._seed_scout(explorer, h, context, scout_result)
            findings = explorer.run()
            logger.info(
                f"[MACS] scout {h.hid}: {explorer.actions_executed} actions, "
                f"stop={explorer.stop_reason}, {len(findings)} findings"
            )
            return findings, scout_result, frontier.visited

        all_findings: list = []
        merged_visited: set = set(prior_visited)
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(run_one, h) for h in scouts]
            for fut in as_completed(futures):
                findings, scout_result, visited = fut.result()
                all_findings.extend(findings)
                merged_visited |= visited
                self._merge_stats(scout_result, result)

        # Persist the union of visited actions so reflection rounds don't redo.
        context.exploration_visited = merged_visited

        locations = self._merge_findings_to_locations(all_findings)

        # Thin-findings fallback: same policy as single-explorer E2.
        strong = [f for f in all_findings if f.relevance >= 5]
        if len(strong) < 3 and config.exploration_fallback_to_freeform:
            logger.info(
                f"[MACS] thin findings across scouts ({len(all_findings)}) — "
                "merging free-form navigation"
            )
            fallback = super(PriorityNavigationAgent, self).run(context, max_iterations)
            fb_locs = (fallback.output or {}).get("suspicious_locations", [])
            seen = {
                l.get("file_path", "") + "::" + str(l.get("function_name", ""))
                for l in fb_locs
            }
            for loc in locations:
                key = loc.get("file_path", "") + "::" + str(loc.get("function_name", ""))
                if key not in seen:
                    fb_locs.append(loc)
            fallback.output["suspicious_locations"] = fb_locs
            self._merge_stats(result, fallback)
            context.suspicious_locations = fb_locs
            context.navigation_was_freeform = True
            return fallback

        context.suspicious_locations = locations
        context.navigation_was_freeform = False
        pruned = list(board.prune_events)
        result.output = {
            "suspicious_locations": locations,
            "investigation_summary": (
                f"Competitive scouting: {n} parallel scouts "
                f"({', '.join(h.hid for h in scouts)}), {len(all_findings)} "
                f"findings, {len(pruned)} cluster(s) pruned."
            ),
        }
        if pruned:
            result.output["pruned_clusters"] = pruned
        result.explanation = result.output["investigation_summary"]
        result.success = True
        context.add_trace(self.name, "competitive_scouting", result.explanation)
        return result

    # ── per-scout helpers ────────────────────────────────────────────────────

    def _seed_scout(
        self, explorer: PriorityExplorer, hypothesis, context: AgentContext,
        result: AgentResult,
    ) -> None:
        """Seed a scout with its OWN hypothesis plus the shared anchors."""
        # Shared anchors (every scout should see the strongest signals)
        for fp in context.stack_trace_files or []:
            explorer.seed("inspect_file", fp, origin="stack_trace")
        for fp in context.test_derived_candidates or []:
            explorer.seed("inspect_file", fp, origin="test_derived")
        for fp in (context.mentioned_files or [])[:5]:
            explorer.seed("inspect_file", fp, origin="mentioned")

        # This scout's own hypothesis: files + probes
        for fp in hypothesis.suspected_files[:5]:
            explorer.seed("inspect_file", fp, origin=hypothesis.hid)
        for probe in hypothesis.probes[:3]:
            target = self._probe_to_query(probe)
            if target:
                explorer.seed(
                    "run_search", target, origin=f"{hypothesis.hid}:probe"
                )
        # Hypothesis statement/component as a search sub-query
        sub = (hypothesis.suspected_component or hypothesis.statement)[:80]
        if sub:
            explorer.seed("run_search", sub, origin=f"{hypothesis.hid}:decomp")

    def _scout_execute(self, action, result, context, board) -> str:
        """Execute an action; serialise graph-touching tools behind graph_lock."""
        if action.kind in ("expand_callers", "expand_callees") or (
            action.kind == "run_search" and context.retriever is not None
        ):
            with board.graph_lock:
                return self._execute_action(action, result, context)
        return self._execute_action(action, result, context)

    @staticmethod
    def _board_evidence(board, context, parsed, action, obs) -> None:
        """Route observation evidence through the shared board and prune."""
        # 1) hypothesis evidence → tracker (locked) → prune falsified clusters
        for hid, ev in PriorityNavigationAgent._iter_hypothesis_evidence(parsed, action):
            newly = board.record_evidence(hid, ev)
            if newly:
                board.prune_falsified(newly)
        # 2) repeated low relevance on a file → prune the file
        if obs is not None and obs.file_path:
            board.note_relevance(obs.file_path, obs.relevance)

    def _merge_findings_to_locations(self, findings) -> list[dict]:
        """Dedupe findings across scouts by file::function, keep highest score."""
        best: dict[str, object] = {}
        for obs in findings:
            if obs.kind == "run_search":
                continue
            _, _, entity_part = obs.entity.partition("::")
            key = obs.file_path + "::" + entity_part
            cur = best.get(key)
            if cur is None or obs.relevance > cur.relevance:
                best[key] = obs
        return self._findings_to_locations(
            sorted(best.values(), key=lambda o: o.relevance, reverse=True)
        )

    @staticmethod
    def _merge_stats(src: AgentResult, dst: AgentResult) -> None:
        dst.num_llm_calls += src.num_llm_calls
        dst.num_tool_calls += src.num_tool_calls
        dst.prompt_tokens += src.prompt_tokens
        dst.completion_tokens += src.completion_tokens
        dst.total_tokens += src.total_tokens

"""
E2: Priority-guided exploration engine (OrcaLoca-style).

A deterministic scheduler owns the exploration loop: actions live in a
priority queue, each executed action produces an observation that is scored
by one small tool-free LLM call with O(1) context (no message history).
The LLM never picks the next action — it only scores what it is shown and
proposes new entities/queries, which enter the queue with computed
priorities.

priority(a) = W_LLM   × llm_relevance(parent observation)/10
            + W_GRAPH × 1/(1 + graph_distance(target, anchors))
            + W_SIGNAL × static_prior(target)
"""

from __future__ import annotations

import heapq
import itertools
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Action kinds and the registry tool each maps to (documentation; the
# executor callable is supplied by the agent that owns the run).
ACTION_KINDS = (
    "inspect_file",      # get_file_outline (python) / read_file head (java)
    "inspect_function",  # get_function_source
    "expand_callers",    # find_callers
    "expand_callees",    # find_callees
    "run_search",        # code_search / semantic_file_search
)

# static_prior values by provenance of the target
PRIOR_STACK_TRACE = 1.0
PRIOR_MENTIONED = 0.7
PRIOR_HYPOTHESIS = 0.6   # × hypothesis posterior when E1 is on
PRIOR_PATH_KEYWORD = 0.4
PRIOR_DEFAULT = 0.2

RELEVANT_THRESHOLD = 6     # observation relevance ≥ this becomes a finding
STAGNATION_WINDOW = 5      # consecutive low-relevance observations to stop
STAGNATION_RELEVANCE = 3
EARLY_SUCCESS_COUNT = 8    # findings with relevance ≥ 8 to stop early
EARLY_SUCCESS_RELEVANCE = 8
MAX_FINDINGS = 15


@dataclass
class ExplorationAction:
    """One pending exploration step."""

    priority: float
    kind: str                 # one of ACTION_KINDS
    target: str               # "file.py", "file.py::Class.method", or a query
    origin: str = ""          # anchor / sub-query / parent entity that spawned it
    depth: int = 0

    def key(self) -> tuple[str, str]:
        return (self.kind, self.target)


@dataclass
class Observation:
    """LLM-scored result of one executed action."""

    entity: str
    kind: str
    relevance: int = 0                     # 0-10
    reason: str = ""
    is_likely_fault_location: bool = False
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    new_entities: list[dict] = field(default_factory=list)
    new_queries: list[str] = field(default_factory=list)


class ExplorationFrontier:
    """Priority queue with visited-set and per-entity best-priority dedupe."""

    def __init__(self):
        self._heap: list[tuple[float, int, ExplorationAction]] = []
        self._counter = itertools.count()  # FIFO tie-break for equal priority
        self._best_priority: dict[tuple[str, str], float] = {}
        self.visited: set[tuple[str, str]] = set()

    def push(self, action: ExplorationAction) -> bool:
        """Add an action unless already visited or already queued at ≥ priority."""
        key = action.key()
        if key in self.visited:
            return False
        if self._best_priority.get(key, -1.0) >= action.priority:
            return False
        self._best_priority[key] = action.priority
        heapq.heappush(self._heap, (-action.priority, next(self._counter), action))
        return True

    def pop(self) -> ExplorationAction | None:
        """Pop the highest-priority unvisited action; marks it visited."""
        while self._heap:
            neg_priority, _, action = heapq.heappop(self._heap)
            key = action.key()
            if key in self.visited:
                continue  # stale duplicate left by a priority upgrade
            if self._best_priority.get(key) != -neg_priority:
                continue  # superseded by a higher-priority copy
            self.visited.add(key)
            return action
        return None

    def peek_priority(self) -> float | None:
        while self._heap:
            neg_priority, _, action = self._heap[0]
            key = action.key()
            if key in self.visited or self._best_priority.get(key) != -neg_priority:
                heapq.heappop(self._heap)
                continue
            return -neg_priority
        return None

    def __len__(self) -> int:
        return sum(
            1 for neg, _, a in self._heap
            if a.key() not in self.visited and self._best_priority.get(a.key()) == -neg
        )


class SharedFrontier(ExplorationFrontier):
    """
    Frontier for MACS competitive scouts: a key pruned on the shared board is
    treated exactly like an already-visited key, so a cluster ruled out by one
    scout (falsified hypothesis, or repeated low relevance) is dropped from
    every scout's queue without changing PriorityExplorer's loop.
    """

    def __init__(self, board, scout_id: str = ""):
        super().__init__()
        self._board = board
        self.scout_id = scout_id

    def _blocked(self, action: ExplorationAction) -> bool:
        return self._board.is_pruned(action.key())

    def push(self, action: ExplorationAction) -> bool:
        if self._blocked(action):
            return False
        return super().push(action)

    def pop(self) -> ExplorationAction | None:
        while True:
            action = super().pop()
            if action is None:
                return None
            if self._blocked(action):
                continue  # pruned by another scout after being queued
            return action

    def peek_priority(self) -> float | None:
        while self._heap:
            neg_priority, _, action = self._heap[0]
            key = action.key()
            if (
                key in self.visited
                or self._best_priority.get(key) != -neg_priority
                or self._board.is_pruned(key)
            ):
                heapq.heappop(self._heap)
                continue
            return -neg_priority
        return None

    def __len__(self) -> int:
        return sum(
            1 for neg, _, a in self._heap
            if a.key() not in self.visited
            and self._best_priority.get(a.key()) == -neg
            and not self._board.is_pruned(a.key())
        )


class PriorityExplorer:
    """
    Owns one exploration run: budget, termination, finding assembly.

    The caller supplies two callables so this engine stays free of LLM and
    tool dependencies (unit-testable in isolation):
      execute(action) -> str            # runs the mapped tool, returns output
      observe(action, tool_output) -> Observation   # one small LLM call
    """

    def __init__(
        self,
        execute,
        observe,
        max_actions: int = 20,
        min_priority: float = 0.15,
        w_llm: float = 0.5,
        w_graph: float = 0.3,
        w_signal: float = 0.2,
        max_depth: int = 4,
        graph_distances: dict[str, int] | None = None,
        static_priors: dict[str, float] | None = None,
        frontier: ExplorationFrontier | None = None,
    ):
        self._execute = execute
        self._observe = observe
        self.max_actions = max_actions
        self.min_priority = min_priority
        self.w_llm = w_llm
        self.w_graph = w_graph
        self.w_signal = w_signal
        self.max_depth = max_depth
        self.graph_distances = graph_distances or {}
        self.static_priors = static_priors or {}
        self.frontier = frontier or ExplorationFrontier()
        self.findings: list[Observation] = []
        self.actions_executed = 0
        self.stop_reason = ""

    # ── priority computation ─────────────────────────────────────────────────

    def _target_file(self, target: str) -> str:
        return target.split("::", 1)[0] if "::" in target else target

    def graph_term(self, target: str) -> float:
        fp = self._target_file(target)
        distance = self.graph_distances.get(fp, self.max_depth + 1)
        return 1.0 / (1.0 + distance)

    def static_prior(self, target: str) -> float:
        fp = self._target_file(target)
        return self.static_priors.get(target, self.static_priors.get(fp, PRIOR_DEFAULT))

    def priority(self, target: str, llm_relevance: float = 0.0) -> float:
        return (
            self.w_llm * max(0.0, min(llm_relevance, 10.0)) / 10.0
            + self.w_graph * self.graph_term(target)
            + self.w_signal * self.static_prior(target)
        )

    def seed(self, kind: str, target: str, origin: str = "seed") -> None:
        """Enqueue a seed action (no parent observation → llm term 0)."""
        self.frontier.push(ExplorationAction(
            priority=self.priority(target),
            kind=kind,
            target=target,
            origin=origin,
            depth=0,
        ))

    # ── main loop ────────────────────────────────────────────────────────────

    def run(self) -> list[Observation]:
        low_streak = 0
        while True:
            if self.actions_executed >= self.max_actions:
                self.stop_reason = "max_actions"
                break
            top = self.frontier.peek_priority()
            if top is None:
                self.stop_reason = "frontier_empty"
                break
            if top < self.min_priority:
                self.stop_reason = "below_min_priority"
                break
            strong = [
                f for f in self.findings
                if f.relevance >= EARLY_SUCCESS_RELEVANCE
            ]
            if len(strong) >= EARLY_SUCCESS_COUNT:
                self.stop_reason = "early_success"
                break

            action = self.frontier.pop()
            if action is None:
                self.stop_reason = "frontier_empty"
                break
            self.actions_executed += 1

            try:
                tool_output = self._execute(action)
            except Exception as e:
                logger.debug(f"[Explorer] execute failed for {action.kind}:{action.target}: {e}")
                continue
            if not tool_output:
                continue

            try:
                obs = self._observe(action, tool_output)
            except Exception as e:
                logger.debug(f"[Explorer] observe failed for {action.target}: {e}")
                continue
            if obs is None:
                continue

            if obs.relevance >= RELEVANT_THRESHOLD:
                self.findings.append(obs)
            if obs.relevance < STAGNATION_RELEVANCE:
                low_streak += 1
                if low_streak >= STAGNATION_WINDOW:
                    self.stop_reason = "stagnation"
                    break
            else:
                low_streak = 0

            self._enqueue_offspring(action, obs)

        self.findings.sort(key=lambda o: o.relevance, reverse=True)
        return self.findings[:MAX_FINDINGS]

    def _enqueue_offspring(self, action: ExplorationAction, obs: Observation) -> None:
        if action.depth >= self.max_depth:
            return
        for ent in obs.new_entities or []:
            if not isinstance(ent, dict):
                continue
            target = str(ent.get("target", "")).strip()
            kind = str(ent.get("kind", "inspect_file")).strip()
            if not target or kind not in ACTION_KINDS:
                continue
            self.frontier.push(ExplorationAction(
                priority=self.priority(target, llm_relevance=obs.relevance),
                kind=kind,
                target=target,
                origin=obs.entity,
                depth=action.depth + 1,
            ))
        for q in obs.new_queries or []:
            q = str(q).strip()
            if q:
                self.frontier.push(ExplorationAction(
                    priority=self.priority(q, llm_relevance=obs.relevance),
                    kind="run_search",
                    target=q,
                    origin=obs.entity,
                    depth=action.depth + 1,
                ))

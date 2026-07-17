"""
E1: Competing-hypotheses tracking for scientific debugging.

The ComprehensionAgent proposes K falsifiable hypotheses; the
VerificationAgent (or the priority explorer when E2 is on) labels evidence
for/against each. All belief arithmetic here is deterministic Python —
the LLM only proposes hypotheses and labels evidence, it never sets
posteriors directly.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Contribution of a falsified hypothesis's files in UnifiedScorer (× weight).
# This is the one mechanism that can demote candidates the LLM was
# confidently wrong about.
FALSIFIED_FILE_SCORE = -0.4

_LOG_ODDS_CLAMP = 2.0


@dataclass
class EvidenceProbe:
    """A check that could confirm or disconfirm a hypothesis."""

    description: str = ""
    check_type: str = "code_pattern"  # code_pattern | call_path | data_flow | config
    expected_if_true: str = ""
    query_hint: str = ""  # suggested tool call, e.g. 'code_search("as_sql")'


@dataclass
class EvidenceItem:
    """One observed piece of evidence, labeled by the LLM."""

    probe_description: str = ""
    direction: int = 0  # +1 supports, -1 contradicts
    strength: str = "moderate"  # weak | moderate | strong
    source_tool: str = ""
    location: str = ""  # "file::Class.method" or "file:Lstart-Lend"
    note: str = ""


@dataclass
class Hypothesis:
    """One falsifiable causal claim about the bug's root cause."""

    hid: str = ""
    statement: str = ""
    suspected_component: str = ""
    suspected_files: list[str] = field(default_factory=list)
    suspected_functions: list[str] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)
    probes: list[EvidenceProbe] = field(default_factory=list)
    prior: float = 0.5
    log_odds: float = 0.0
    status: str = "active"  # active | supported | falsified
    evidence: list[EvidenceItem] = field(default_factory=list)

    def __post_init__(self):
        p = min(0.95, max(0.05, float(self.prior or 0.5)))
        self.log_odds = max(
            -_LOG_ODDS_CLAMP, min(_LOG_ODDS_CLAMP, math.log(p / (1 - p)))
        )

    @property
    def posterior(self) -> float:
        return 1.0 / (1.0 + math.exp(-self.log_odds))

    def unchecked_probes(self) -> list[EvidenceProbe]:
        checked = {e.probe_description for e in self.evidence}
        return [p for p in self.probes if p.description not in checked]


class HypothesisTracker:
    """Deterministic belief tracking over competing hypotheses."""

    STRENGTH_LLR = {"weak": 0.25, "moderate": 0.6, "strong": 1.1}
    FALSIFY_THRESHOLD = 0.15
    SUPPORT_THRESHOLD = 0.75

    def __init__(
        self,
        hypotheses: list[Hypothesis],
        falsify_threshold: float | None = None,
    ):
        self.hypotheses: list[Hypothesis] = list(hypotheses)
        if falsify_threshold is not None:
            self.FALSIFY_THRESHOLD = falsify_threshold
        self._by_id = {h.hid: h for h in self.hypotheses}

    def get(self, hid: str) -> Hypothesis | None:
        return self._by_id.get(hid)

    def update(self, hid: str, evidence: EvidenceItem) -> None:
        """Apply one labeled evidence item: log_odds += direction × LLR."""
        h = self._by_id.get(hid)
        if h is None:
            logger.debug(f"[HypothesisTracker] unknown hid '{hid}' — ignored")
            return
        direction = 1 if evidence.direction > 0 else -1 if evidence.direction < 0 else 0
        if direction == 0:
            return
        llr = self.STRENGTH_LLR.get(evidence.strength, self.STRENGTH_LLR["moderate"])
        h.log_odds += direction * llr
        h.evidence.append(evidence)
        self._refresh_status(h)

    def _refresh_status(self, h: Hypothesis) -> None:
        p = h.posterior
        if p < self.FALSIFY_THRESHOLD:
            h.status = "falsified"
        elif p > self.SUPPORT_THRESHOLD:
            h.status = "supported"
        else:
            h.status = "active"

    def surviving(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status != "falsified"]

    def falsified(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status == "falsified"]

    def top_prior(self) -> float:
        return max((h.prior for h in self.hypotheses), default=0.0)

    def surviving_files_ordered(self) -> list[str]:
        """Files of surviving hypotheses, highest posterior first, deduped."""
        seen: set[str] = set()
        ordered: list[str] = []
        for h in sorted(self.surviving(), key=lambda x: x.posterior, reverse=True):
            for fp in h.suspected_files:
                fp = (fp or "").strip()
                if fp and fp not in seen:
                    seen.add(fp)
                    ordered.append(fp)
        return ordered

    def file_scores(self) -> dict[str, float]:
        """
        Per-file signal for UnifiedScorer: max posterior over surviving
        hypotheses; files appearing ONLY in falsified hypotheses get
        FALSIFIED_FILE_SCORE (negative) so they can be demoted.
        """
        scores: dict[str, float] = {}
        for h in self.surviving():
            for fp in h.suspected_files:
                fp = (fp or "").strip()
                if fp:
                    scores[fp] = max(scores.get(fp, 0.0), h.posterior)
        for h in self.falsified():
            for fp in h.suspected_files:
                fp = (fp or "").strip()
                if fp and fp not in scores:
                    scores[fp] = FALSIFIED_FILE_SCORE
        return scores

    def reflection_summary(self) -> str:
        """Targeted retry guidance: what was falsified, what remains unchecked."""
        lines: list[str] = []
        for h in self.falsified():
            strongest = ""
            contra = [e for e in h.evidence if e.direction < 0]
            if contra:
                best = max(
                    contra,
                    key=lambda e: self.STRENGTH_LLR.get(e.strength, 0.0),
                )
                loc = f" at {best.location}" if best.location else ""
                strongest = f" ({best.note}{loc})" if best.note else loc
            lines.append(
                f"{h.hid} FALSIFIED: {h.statement}{strongest}. "
                "Do NOT re-investigate this direction."
            )
        for h in sorted(self.surviving(), key=lambda x: x.posterior, reverse=True):
            unchecked = h.unchecked_probes()
            probe_note = ""
            if unchecked:
                probe_note = (
                    f" Unchecked probes: "
                    + "; ".join(p.description for p in unchecked[:2])
                )
            lines.append(
                f"{h.hid} (posterior {h.posterior:.2f}): {h.statement}."
                f"{probe_note} Suspected files: {', '.join(h.suspected_files[:4])}"
            )
        if not lines:
            return ""
        return (
            "Hypothesis verification status — focus the retry on surviving "
            "hypotheses and their unchecked probes:\n"
            + "\n".join(f"- {l}" for l in lines)
        )

    def to_dict(self) -> list[dict]:
        """Serializable snapshot for instrumentation/analysis."""
        return [
            {
                "hid": h.hid,
                "statement": h.statement,
                "suspected_component": h.suspected_component,
                "suspected_files": h.suspected_files,
                "prior": h.prior,
                "posterior": round(h.posterior, 4),
                "status": h.status,
                "num_evidence": len(h.evidence),
            }
            for h in self.hypotheses
        ]


def parse_hypotheses_from_llm(
    raw, fallback_statement: str = "", max_k: int = 5
) -> list[Hypothesis]:
    """
    Lenient parse of the ``hypotheses`` array from comprehension output.
    Dedupes by suspected_component (first wins). On failure, wraps
    ``fallback_statement`` as a single hypothesis so the pipeline degrades
    to today's single-hypothesis behavior.
    """
    hypotheses: list[Hypothesis] = []
    seen_components: set[str] = set()

    if isinstance(raw, list):
        for i, entry in enumerate(raw[:max_k * 2]):
            if not isinstance(entry, dict):
                continue
            statement = str(entry.get("statement", "")).strip()
            if not statement:
                continue
            component = str(entry.get("suspected_component", "")).strip()
            comp_key = component.lower()
            if comp_key and comp_key in seen_components:
                continue  # hypothesis collapse guard: distinct components only
            if comp_key:
                seen_components.add(comp_key)

            probes = []
            for p in entry.get("probes") or []:
                if isinstance(p, dict) and p.get("description"):
                    probes.append(EvidenceProbe(
                        description=str(p["description"]),
                        check_type=str(p.get("check_type", "code_pattern")),
                        expected_if_true=str(p.get("expected_if_true", "")),
                        query_hint=str(p.get("query_hint", "")),
                    ))
            try:
                prior = float(entry.get("prior", 0.5))
            except (TypeError, ValueError):
                prior = 0.5

            files = [
                str(f).strip() for f in (entry.get("suspected_files") or [])
                if str(f).strip()
            ]
            functions = [
                str(f).strip() for f in (entry.get("suspected_functions") or [])
                if str(f).strip()
            ]
            chain = [str(c) for c in (entry.get("causal_chain") or [])]

            hypotheses.append(Hypothesis(
                hid=str(entry.get("hid", "")).strip() or f"HYP{len(hypotheses) + 1}",
                statement=statement,
                suspected_component=component,
                suspected_files=files,
                suspected_functions=functions,
                causal_chain=chain,
                probes=probes,
                prior=prior,
            ))
            if len(hypotheses) >= max_k:
                break

    if not hypotheses and fallback_statement.strip():
        hypotheses = [Hypothesis(
            hid="HYP1",
            statement=fallback_statement.strip()[:500],
            prior=0.6,
        )]
        logger.debug(
            "[Hypothesis] LLM hypotheses unparsable — degraded to single "
            "fallback hypothesis"
        )

    # Ensure unique hids
    seen_hids: set[str] = set()
    for i, h in enumerate(hypotheses):
        if h.hid in seen_hids:
            h.hid = f"HYP{i + 1}"
        seen_hids.add(h.hid)
    return hypotheses

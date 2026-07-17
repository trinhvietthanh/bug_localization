"""
E1: Hypothesis Verification Agent.

Runs after Navigation round 1, before Confirmation. Does NOT hunt for the
bug — it gathers evidence FOR and AGAINST each competing hypothesis by
reading actual code, then reports labeled evidence that the deterministic
HypothesisTracker turns into posterior updates.
"""

import logging

from agents.base_agent import BaseAgent, AgentContext, AgentResult
from agents.hypothesis import EvidenceItem
from tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Hypothesis Verification Agent. You are given competing hypotheses \
about a bug's root cause. Your job is NOT to find the bug — it is to gather evidence FOR and \
AGAINST each hypothesis by reading actual code.

For each hypothesis's probes: run the suggested tool (or a better one), then record what you \
observed. You MUST look for disconfirming evidence with the same effort as confirming evidence. \
A hypothesis you falsify is as valuable as one you support.

Evidence strength guide:
- "strong": direct observation of the mechanism (the guilty code path exists / provably cannot exist)
- "moderate": consistent circumstantial evidence (the pattern is present but the trigger is unverified)
- "weak": suggestive naming/structure only

When done (or out of tool budget), respond with a JSON block:

```json
{
  "verdicts": [
    {
      "hid": "HYP1",
      "evidence": [
        {
          "probe_description": "which probe this addresses",
          "direction": 1,
          "strength": "moderate",
          "source_tool": "read_file",
          "location": "path/to/file.py::Class.method",
          "note": "what was actually observed, <25 words"
        }
      ]
    }
  ],
  "new_suspect_files": ["files discovered during verification that no hypothesis listed"]
}
```
direction: 1 = supports the hypothesis, -1 = contradicts it."""


class VerificationAgent(BaseAgent):
    """Agent that verifies/falsifies competing hypotheses with code evidence."""

    TOOLS = ["read_file", "get_function_source", "code_search", "find_callers", "find_callees"]

    def __init__(self):
        super().__init__(name="HypothesisVerification")
        for name in self.TOOLS:
            if name in TOOL_REGISTRY:
                self.register_tool(name, *TOOL_REGISTRY[name])

    def get_system_prompt(self, context: AgentContext) -> str:
        return SYSTEM_PROMPT

    def get_initial_message(self, context: AgentContext) -> str:
        parts = [
            f"## Repository\n- Language: {context.language}\n",
            f"## Bug Summary\n{context.problem_statement[:1500]}\n",
            "## Competing Hypotheses to Verify",
        ]
        for h in context.hypotheses:
            lines = [
                f"### {h.hid} (prior {h.prior:.2f}): {h.statement}",
                f"- Component: {h.suspected_component}",
                f"- Files: {', '.join(h.suspected_files[:5]) or 'none listed'}",
            ]
            if h.causal_chain:
                lines.append(f"- Causal chain: {' → '.join(h.causal_chain[:4])}")
            for i, p in enumerate(h.probes[:4], 1):
                hint = f" [try: {p.query_hint}]" if p.query_hint else ""
                lines.append(
                    f"- Probe {i}: {p.description} — expected if true: "
                    f"{p.expected_if_true}{hint}"
                )
            parts.append("\n".join(lines))

        if context.candidate_files:
            parts.append(
                "## Files Navigation Already Flagged\n"
                + "\n".join(f"- {f}" for f in context.candidate_files[:10])
            )

        parts.append(
            "\nVerify each hypothesis with the tools. Prioritize probes marked "
            "DISCONFIRMING. Then report your labeled evidence as the JSON block."
        )
        return "\n\n".join(parts)

    def process_result(self, result: AgentResult, context: AgentContext):
        """Feed labeled evidence into the tracker; absorb new suspect files."""
        tracker = context.hypothesis_tracker
        if tracker is None:
            return

        output = result.output or {}
        applied = 0
        for verdict in output.get("verdicts") or []:
            if not isinstance(verdict, dict):
                continue
            hid = str(verdict.get("hid", "")).strip()
            for ev in verdict.get("evidence") or []:
                if not isinstance(ev, dict):
                    continue
                try:
                    direction = int(ev.get("direction", 0))
                except (TypeError, ValueError):
                    direction = 0
                strength = str(ev.get("strength", "moderate")).lower()
                if strength not in ("weak", "moderate", "strong"):
                    strength = "moderate"
                tracker.update(hid, EvidenceItem(
                    probe_description=str(ev.get("probe_description", "")),
                    direction=direction,
                    strength=strength,
                    source_tool=str(ev.get("source_tool", "")),
                    location=str(ev.get("location", "")),
                    note=str(ev.get("note", "")),
                ))
                applied += 1

        # New suspects discovered during verification — additive only
        for fp in output.get("new_suspect_files") or []:
            fp = str(fp).strip()
            if fp and fp not in context.candidate_files:
                context.candidate_files.append(fp)

        surviving = len(tracker.surviving())
        falsified = len(tracker.falsified())
        logger.info(
            f"[VerificationAgent] applied {applied} evidence items — "
            f"{surviving} surviving, {falsified} falsified"
        )
        context.add_trace(
            self.name,
            "verification_summary",
            "; ".join(
                f"{h['hid']}:{h['status']}(p={h['posterior']})"
                for h in tracker.to_dict()
            ),
        )

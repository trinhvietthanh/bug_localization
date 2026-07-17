"""
E3: Hierarchical narrowing + listwise rerank (Agentless-style).

Runs after unified scoring. Stage (a) narrows each top-K file to suspect
functions/line ranges with one structured LLM call over file skeletons.
Stage (b) re-orders the top-K files with one listwise LLM call over
per-candidate "evidence cards".

Invariants:
- Permutation only: the set of top-K files never changes, the tail of
  ranked_files is untouched — Top-10/recall are unaffected by construction.
- Fail open: any LLM/parse failure leaves the result exactly as it was.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from typing import Any, Optional

from config import config
from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Cap per-card snippet size so K=10 cards stay ~5k tokens total
MAX_SNIPPET_LINES = 15
MAX_CARD_VERDICT_CHARS = 300


class _RerankLLM(BaseAgent):
    """Minimal concrete BaseAgent used only for its shared LLM client."""

    def __init__(self):
        super().__init__(name="ListwiseReranker")

    def get_system_prompt(self, context) -> str:  # pragma: no cover - unused
        return ""

    def get_initial_message(self, context) -> str:  # pragma: no cover - unused
        return ""


def deterministic_shuffle(items: list, seed: str) -> list:
    """Shuffle reproducibly from a string seed (stable across processes)."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled


def validate_permutation(
    ranking: list[str], valid_ids: list[str], fallback_order: list[str]
) -> list[str]:
    """
    Coerce an LLM-produced ID list into a valid permutation of ``valid_ids``.

    Unknown IDs and duplicates are dropped; missing IDs are appended in
    ``fallback_order`` (the pre-rerank order) so a partial answer degrades
    toward the existing ranking instead of losing candidates.
    """
    valid = set(valid_ids)
    seen: set[str] = set()
    cleaned: list[str] = []
    for rid in ranking or []:
        rid = str(rid).strip()
        if rid in valid and rid not in seen:
            seen.add(rid)
            cleaned.append(rid)
    for rid in fallback_order:
        if rid not in seen:
            seen.add(rid)
            cleaned.append(rid)
    return cleaned


def build_evidence_cards(
    top_files: list[str],
    unified_scores: list[dict],
    ranked_locations: list[dict],
    snippets: Optional[dict[str, str]] = None,
) -> list[dict]:
    """
    Assemble one serializable evidence card per candidate file.

    Cards carry qualitative signal flags only — never the unified total score
    or the current rank — so the rerank LLM judges evidence, not position.
    """
    score_map = {s.get("file_path", ""): s for s in unified_scores or []}
    verdict_map: dict[str, dict] = {}
    for loc in ranked_locations or []:
        fp = loc.get("file_path", "")
        if fp and fp not in verdict_map and loc.get("explanation"):
            verdict_map[fp] = loc

    cards = []
    for fp in top_files:
        s = score_map.get(fp, {})
        signals = []
        if s.get("stack_trace_score", 0) > 0:
            signals.append("STACK_TRACE")
        if s.get("error_match_score", 0) > 0:
            signals.append("ERROR_MATCH")
        if s.get("mentioned_score", 0) > 0:
            signals.append("MENTIONED_IN_REPORT")
        if s.get("graph_score", 0) > 0:
            signals.append(f"graph {s['graph_score']:.2f}")
        if s.get("semantic_score", 0) > 0:
            signals.append(f"semantic {s['semantic_score']:.2f}")
        if s.get("git_recency_score", 0) > 0:
            signals.append("recently_changed")
        if s.get("penalty", 0) > 0:
            signals.append("TEST_FILE")

        loc = verdict_map.get(fp)
        if loc:
            verdict = str(loc.get("explanation", ""))[:MAX_CARD_VERDICT_CHARS]
            confidence = loc.get("confidence", 0.0)
        else:
            verdict = "none (pool candidate — added for recall, not inspected by agents)"
            confidence = None

        cards.append({
            "file_path": fp,
            "signals": signals,
            "llm_confidence": s.get("llm_confidence", 0.0),
            "agent_verdict": verdict,
            "agent_confidence": confidence,
            "snippet": (snippets or {}).get(fp, ""),
        })
    return cards


def render_card(card_id: str, card: dict) -> str:
    """Render one evidence card as prompt text."""
    lines = [f"[{card_id}] {card['file_path']}"]
    lines.append(
        "  signals: " + (" | ".join(card["signals"]) if card["signals"] else "none")
    )
    conf = card.get("agent_confidence")
    conf_str = f" (confidence {conf:.2f})" if isinstance(conf, (int, float)) else ""
    lines.append(f"  agent verdict: {card['agent_verdict']}{conf_str}")
    if card.get("snippet"):
        lines.append("  suspect code:")
        for sl in card["snippet"].splitlines()[:MAX_SNIPPET_LINES]:
            lines.append(f"    {sl}")
    return "\n".join(lines)


NARROWING_SYSTEM_PROMPT = """You are a fault localization expert. Given a bug summary and the \
structural skeletons of candidate source files, identify for EACH file up to 2 functions/methods \
most likely to contain the fault, with approximate line ranges if you can infer them.

Respond with JSON only:
{"files": [{"file": "path", "functions": [{"name": "func_or_Class.method", "start_line": 0, "end_line": 0, "reason": "<15 words"}]}]}
Use start_line/end_line 0 when unknown. Include every file given, even with an empty functions list."""

RERANK_SYSTEM_PROMPT = """You are ranking candidate files for the fix location of a bug. \
The candidate cards below are presented in RANDOM order.

Rules:
- Judge ONLY from the evidence shown on each card.
- A strong, specific agent verdict beats weak circumstantial signals.
- STACK_TRACE and ERROR_MATCH are strong mechanical evidence.
- Files with 'agent verdict: none' need strong signals to rank high.
- Output every card ID exactly once.

Respond with JSON only:
{"ranking": ["C3", "C1", ...], "confidence": {"C3": 0.8, ...}, "top1_justification": "<40 words"}"""


class ListwiseReranker:
    """Two-stage E3 pipeline: hierarchical narrowing + listwise rerank."""

    def __init__(self, llm: Any = None):
        # ``llm`` is injectable for tests / offline card replay; it must expose
        # chat.completions.create like the OpenAI client.
        self._llm_agent = None
        self._injected_llm = llm

    @property
    def _client(self):
        if self._injected_llm is not None:
            return self._injected_llm
        if self._llm_agent is None:
            self._llm_agent = _RerankLLM()
        return self._llm_agent.client

    def _complete(self, system: str, user: str) -> tuple[str, dict]:
        """One tool-free, temperature-0 LLM call. Returns (content, usage)."""
        timeout = config.llm_call_timeout if config.llm_call_timeout > 0 else None
        response = self._client.chat.completions.create(
            model=config.llm.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=config.llm.max_tokens,
            timeout=timeout,
        )
        usage = getattr(response, "usage", None)
        usage_dict = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        return response.choices[0].message.content or "", usage_dict

    @staticmethod
    def _parse_json(content: str) -> Optional[dict]:
        import re

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fenced:
            parsed = BaseAgent._loads_lenient(fenced.group(1))
            if parsed is not None:
                return parsed
        brace = re.search(r"\{[\s\S]*\}", content)
        if brace:
            parsed = BaseAgent._loads_lenient(brace.group(0))
            if parsed is not None:
                return parsed
        return None

    # ── Stage (a): hierarchical narrowing ────────────────────────────────────

    def narrow(
        self, result, context, top_files: list[str]
    ) -> dict[str, list[dict]]:
        """
        file → [{name, start_line, end_line, reason}] for the top-K files.
        Returns {} on any failure (fail open).
        """
        from tools.repo_skeleton import skeleton_for_files

        try:
            skeletons = skeleton_for_files(
                context.repo_path, top_files, context.language
            )
        except Exception as e:
            logger.debug(f"[Reranker] skeleton build failed: {e}")
            return {}

        bug_summary = (
            context.structured_bug_info.get("bug_phenomenon", "")
            if context.structured_bug_info
            else ""
        ) or context.problem_statement[:1000]

        parts = [f"Bug summary:\n{bug_summary}\n", "Candidate files:"]
        for fp in top_files:
            sk = skeletons.get(fp, "")
            parts.append(f"- {fp}: [{sk}]" if sk else f"- {fp}: [no skeleton]")
        user_msg = "\n".join(parts)

        try:
            content, usage = self._complete(NARROWING_SYSTEM_PROMPT, user_msg)
            self._track(result, usage)
        except Exception as e:
            logger.warning(f"[Reranker] narrowing LLM call failed: {e}")
            return {}

        parsed = self._parse_json(content)
        if not parsed or not isinstance(parsed.get("files"), list):
            logger.debug("[Reranker] narrowing output unparsable — skipping")
            return {}

        narrowed: dict[str, list[dict]] = {}
        top_set = set(top_files)
        for entry in parsed["files"]:
            if not isinstance(entry, dict):
                continue
            fp = str(entry.get("file", "")).strip()
            if fp not in top_set:
                continue
            funcs = []
            for fn in (entry.get("functions") or [])[:2]:
                if not isinstance(fn, dict) or not fn.get("name"):
                    continue
                try:
                    start = int(fn.get("start_line", 0) or 0)
                    end = int(fn.get("end_line", 0) or 0)
                except (TypeError, ValueError):
                    start, end = 0, 0
                funcs.append({
                    "name": str(fn["name"]),
                    "start_line": start,
                    "end_line": end,
                    "reason": str(fn.get("reason", ""))[:120],
                })
            if funcs:
                narrowed[fp] = funcs
        return narrowed

    def apply_narrowing(
        self, result, context, narrowed: dict[str, list[dict]]
    ) -> dict[str, str]:
        """
        Fill function/line info into ranked_locations for files lacking it and
        append missing entries (additive only). Returns per-file snippets for
        the evidence cards.
        """
        located = {loc.get("file_path") for loc in result.ranked_locations}
        snippets: dict[str, str] = {}
        for fp, funcs in narrowed.items():
            primary = funcs[0]
            # Fill gaps in existing entries for this file
            filled = False
            for loc in result.ranked_locations:
                if loc.get("file_path") == fp and not loc.get("function_name"):
                    loc["function_name"] = primary["name"].rsplit(".", 1)[-1]
                    if "." in primary["name"]:
                        loc.setdefault("class_name", primary["name"].rsplit(".", 1)[0])
                    loc["start_line"] = primary["start_line"]
                    loc["end_line"] = primary["end_line"]
                    filled = True
                    break
            if not filled and fp not in located:
                result.ranked_locations.append({
                    "rank": len(result.ranked_locations) + 1,
                    "file_path": fp,
                    "function_name": primary["name"].rsplit(".", 1)[-1],
                    "class_name": (
                        primary["name"].rsplit(".", 1)[0]
                        if "." in primary["name"] else ""
                    ),
                    "start_line": primary["start_line"],
                    "end_line": primary["end_line"],
                    "confidence": 0.0,
                    "explanation": primary["reason"],
                    "source": "hierarchical_narrowing",
                })
            snippet = self._read_snippet(
                context.repo_path, fp, primary["start_line"], primary["end_line"]
            )
            if snippet:
                snippets[fp] = snippet

        # Refresh ranked_methods with any newly named functions
        from evaluation.metrics import extract_methods_from_locations

        for m in extract_methods_from_locations(result.ranked_locations):
            if m not in result.ranked_methods:
                result.ranked_methods.append(m)
        return snippets

    @staticmethod
    def _read_snippet(repo_path: str, rel_path: str, start: int, end: int) -> str:
        if start <= 0:
            return ""
        abs_path = os.path.join(repo_path, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            return ""
        if start > len(lines):
            return ""
        stop = min(end if end >= start else start, len(lines), start + MAX_SNIPPET_LINES - 1)
        return "".join(lines[start - 1:stop]).rstrip()

    # ── Stage (b): listwise rerank ───────────────────────────────────────────

    def rerank_cards(
        self,
        cards: list[dict],
        instance_id: str,
        unified_order: list[str],
        result=None,
        bug_summary: str = "",
        passes: int = 1,
    ) -> tuple[list[str], dict[str, float]]:
        """
        Rank card files via listwise LLM call(s). Pure given the LLM responses,
        so saved cards can be replayed offline. Returns (file_order, confidence).
        On total failure returns (unified_order, {}).
        """
        if len(cards) < 2:
            return unified_order, {}

        ids = [f"C{i + 1}" for i in range(len(cards))]
        id_to_file = {cid: c["file_path"] for cid, c in zip(ids, cards)}
        file_to_id = {v: k for k, v in id_to_file.items()}
        fallback_ids = [file_to_id[fp] for fp in unified_order if fp in file_to_id]

        pass_orders: list[list[str]] = []
        confidences: dict[str, float] = {}
        for p in range(max(1, passes)):
            shuffled = deterministic_shuffle(
                list(zip(ids, cards)), seed=f"{instance_id}:{p}"
            )
            body = "\n\n".join(render_card(cid, c) for cid, c in shuffled)
            user_msg = (
                (f"Bug summary:\n{bug_summary}\n\n" if bug_summary else "")
                + f"Candidates ({len(cards)} cards, random order):\n\n{body}"
            )
            try:
                content, usage = self._complete(RERANK_SYSTEM_PROMPT, user_msg)
                if result is not None:
                    self._track(result, usage)
            except Exception as e:
                logger.warning(f"[Reranker] rerank LLM call failed (pass {p}): {e}")
                continue

            parsed = self._parse_json(content)
            if not parsed:
                logger.debug(f"[Reranker] rerank output unparsable (pass {p})")
                continue
            order_ids = validate_permutation(
                parsed.get("ranking") or [], ids, fallback_ids
            )
            pass_orders.append([id_to_file[cid] for cid in order_ids])
            for cid, conf in (parsed.get("confidence") or {}).items():
                fp = id_to_file.get(str(cid).strip())
                if fp is None:
                    continue
                try:
                    confidences[fp] = max(confidences.get(fp, 0.0), float(conf))
                except (TypeError, ValueError):
                    pass

        if not pass_orders:
            return unified_order, {}
        if len(pass_orders) == 1:
            return pass_orders[0], confidences

        from utils.ranking import reciprocal_rank_fusion

        fused = [fp for fp, _ in reciprocal_rank_fusion(pass_orders)]
        return validate_permutation(fused, list(file_to_id), unified_order), confidences

    # ── Entry point ──────────────────────────────────────────────────────────

    def rerank(self, result, context) -> None:
        """
        Run enabled E3 stages in place on ``result``. Never raises; never
        changes pool membership or the tail beyond top-K.
        """
        top_k = max(2, config.listwise_rerank_top_k)
        top_files = list(result.ranked_files[:top_k])
        tail = list(result.ranked_files[top_k:])
        if len(top_files) < 2:
            return

        snippets: dict[str, str] = {}
        if config.enable_hierarchical_narrowing:
            try:
                narrowed = self.narrow(result, context, top_files)
                if narrowed:
                    snippets = self.apply_narrowing(result, context, narrowed)
            except Exception as e:
                logger.warning(f"[Reranker] narrowing failed (ignored): {e}")

        if not config.enable_listwise_rerank:
            return

        try:
            unified_scores = result.agent_results.get("unified_scores", [])
            cards = build_evidence_cards(
                top_files, unified_scores, result.ranked_locations, snippets
            )
            bug_summary = (
                context.structured_bug_info.get("bug_phenomenon", "")
                if context.structured_bug_info
                else ""
            ) or context.problem_statement[:800]

            new_order, confidences = self.rerank_cards(
                cards,
                instance_id=context.instance_id,
                unified_order=top_files,
                result=result,
                bug_summary=bug_summary,
                passes=config.listwise_rerank_passes,
            )
            if set(new_order) != set(top_files):
                logger.warning("[Reranker] permutation invariant violated — skipping")
                return

            result.agent_results["listwise_rerank"] = {
                "pre_order": top_files,
                "post_order": new_order,
                "cards": cards,
                "passes": config.listwise_rerank_passes,
            }
            result.ranked_files = new_order + tail

            for loc in result.ranked_locations:
                fp = loc.get("file_path", "")
                if fp in confidences:
                    loc["rerank_confidence"] = round(confidences[fp], 3)

            # Re-align location ranks with the new file order
            order_index = {fp: i for i, fp in enumerate(result.ranked_files)}
            result.ranked_locations.sort(
                key=lambda x: (
                    order_index.get(x.get("file_path", ""), len(order_index)),
                    x.get("rank", 0),
                )
            )
            for rank, loc in enumerate(result.ranked_locations, 1):
                loc["rank"] = rank
        except Exception as e:
            logger.warning(f"[Reranker] listwise rerank failed (ignored): {e}")

    @staticmethod
    def _track(result, usage: dict) -> None:
        if result is None:
            return
        result.total_llm_calls += 1
        result.total_prompt_tokens += usage.get("prompt_tokens", 0)
        result.total_completion_tokens += usage.get("completion_tokens", 0)
        result.total_tokens += usage.get("total_tokens", 0)

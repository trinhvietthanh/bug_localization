"""Unit tests for MACS (Multi-Agent Competitive Scouting) shared state.

Pure-Python logic — no LLM/tools needed. Covers the SharedScoutBoard blackboard
(pruning triggers, thread-safety) and the SharedFrontier pruning integration.
"""

import threading

import pytest

from agents.hypothesis import EvidenceItem, Hypothesis, HypothesisTracker
from agents.scout_board import SharedScoutBoard
from core.explorer import ExplorationAction, SharedFrontier
from config import config


def _strong(direction):
    return EvidenceItem(
        probe_description="t", direction=direction, strength="strong",
        source_tool="inspect_file", location="x",
    )


def _tracker():
    hyps = [
        Hypothesis(hid="HYP1", statement="a", suspected_files=["a.py", "shared.py"], prior=0.5),
        Hypothesis(hid="HYP2", statement="b", suspected_files=["b.py", "shared.py"], prior=0.5),
    ]
    return HypothesisTracker(hyps), hyps


# ── is_pruned ────────────────────────────────────────────────────────────────

def test_is_pruned_reflects_files_and_keys():
    board = SharedScoutBoard()
    assert board.is_pruned(("inspect_file", "a.py")) is False  # fast path, nothing pruned
    board.pruned_files.add("a.py")
    assert board.is_pruned(("inspect_file", "a.py")) is True
    assert board.is_pruned(("inspect_function", "a.py::foo")) is True  # target-file match
    assert board.is_pruned(("inspect_file", "b.py")) is False
    board.pruned_keys.add(("run_search", "b.py"))
    assert board.is_pruned(("run_search", "b.py")) is True


# ── record_evidence / prune_falsified ─────────────────────────────────────────

def test_record_evidence_reports_newly_falsified():
    tracker, _ = _tracker()
    board = SharedScoutBoard(tracker)
    # one strong disconfirming: posterior ~0.25, not yet falsified
    assert board.record_evidence("HYP1", _strong(-1)) == []
    # second strong disconfirming crosses the 0.15 falsify threshold
    assert board.record_evidence("HYP1", _strong(-1)) == ["HYP1"]
    # once falsified, further evidence doesn't re-report it as "newly"
    assert board.record_evidence("HYP1", _strong(-1)) == []


def test_prune_falsified_spares_shared_surviving_files():
    tracker, _ = _tracker()
    board = SharedScoutBoard(tracker)
    board.record_evidence("HYP1", _strong(-1))
    newly = board.record_evidence("HYP1", _strong(-1))
    assert newly == ["HYP1"]
    pruned = board.prune_falsified(newly)
    # a.py belongs ONLY to falsified HYP1 → pruned; shared.py still in surviving
    # HYP2 → spared
    assert "a.py" in pruned
    assert "shared.py" not in pruned
    assert board.is_pruned(("inspect_file", "a.py")) is True
    assert board.is_pruned(("inspect_file", "shared.py")) is False


def test_record_evidence_no_tracker_is_noop():
    board = SharedScoutBoard(tracker=None)
    assert board.record_evidence("HYP1", _strong(-1)) == []
    assert board.prune_falsified(["HYP1"]) == []


# ── note_relevance ─────────────────────────────────────────────────────────────

def test_note_relevance_prunes_after_low_streak():
    board = SharedScoutBoard()
    lo = config.scouting_prune_relevance - 1
    # first low observation: streak = 1 (< scouting_prune_low_streak=2) → not pruned
    assert board.note_relevance("c.py", lo) is False
    # second consecutive low: reaches the streak → pruned
    assert board.note_relevance("c.py", lo) is True
    assert board.is_pruned(("inspect_file", "c.py")) is True


def test_note_relevance_high_resets_streak():
    board = SharedScoutBoard()
    lo = config.scouting_prune_relevance - 1
    hi = config.scouting_prune_relevance + 5
    board.note_relevance("d.py", lo)      # streak 1
    board.note_relevance("d.py", hi)      # reset
    assert board.note_relevance("d.py", lo) is False  # streak back to 1, not pruned
    assert board.is_pruned(("inspect_file", "d.py")) is False


# ── SharedFrontier integration ─────────────────────────────────────────────────

def test_shared_frontier_skips_pruned_on_push():
    board = SharedScoutBoard()
    board.pruned_files.add("a.py")
    fr = SharedFrontier(board, scout_id="HYP1")
    assert fr.push(ExplorationAction(priority=1.0, kind="inspect_file", target="a.py")) is False
    assert fr.push(ExplorationAction(priority=1.0, kind="inspect_file", target="b.py")) is True


def test_shared_frontier_skips_pruned_after_queueing():
    board = SharedScoutBoard()
    fr = SharedFrontier(board, scout_id="HYP1")
    fr.push(ExplorationAction(priority=0.9, kind="inspect_file", target="a.py"))
    fr.push(ExplorationAction(priority=0.5, kind="inspect_file", target="b.py"))
    # another scout prunes a.py after it was queued
    board.pruned_files.add("a.py")
    assert fr.peek_priority() == pytest.approx(0.5)
    action = fr.pop()
    assert action.target == "b.py"
    assert fr.pop() is None


# ── concurrency ────────────────────────────────────────────────────────────────

def test_concurrent_updates_lose_no_evidence():
    # 3 threads each apply many weak SUPPORTING items to distinct hypotheses;
    # under the board lock every update must land (no read-modify-write races).
    hyps = [Hypothesis(hid=f"HYP{i}", statement=str(i), prior=0.5) for i in range(3)]
    tracker = HypothesisTracker(hyps)
    board = SharedScoutBoard(tracker)
    n = 500

    def worker(hid):
        for _ in range(n):
            board.record_evidence(hid, EvidenceItem(
                probe_description="t", direction=1, strength="weak", source_tool="x",
            ))

    threads = [threading.Thread(target=worker, args=(f"HYP{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(3):
        assert len(tracker.get(f"HYP{i}").evidence) == n


def test_concurrent_note_relevance_prunes_each_file_once():
    board = SharedScoutBoard()
    lo = config.scouting_prune_relevance - 1
    files = [f"f{i}.py" for i in range(20)]

    def worker():
        for fp in files:
            for _ in range(5):
                board.note_relevance(fp, lo)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for fp in files:
        assert board.is_pruned(("inspect_file", fp)) is True
    # each file pruned exactly once despite concurrent writers
    prune_files = [e for e in board.prune_events if "low relevance" in e]
    assert len(prune_files) == len(files)

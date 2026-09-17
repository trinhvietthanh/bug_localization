"""
MACS: Shared Graph State (blackboard) for Multi-Agent Competitive Scouting.

When 2-3 Explorer agents scout competing hypotheses in parallel, they share
one ``SharedScoutBoard``. It centralises the two things that must be seen by
every scout the moment one scout learns them:

  1. A hypothesis proven wrong. Evidence flows through ``record_evidence`` into
     the (unlocked) ``HypothesisTracker`` under this board's lock; when a
     hypothesis crosses the falsify threshold, every file that belongs ONLY to
     falsified hypotheses is added to ``pruned_files`` (``prune_falsified``).
  2. A file that keeps scoring low. ``note_relevance`` tracks a per-file
     low-relevance streak; once it reaches ``scouting_prune_low_streak`` the
     file is pruned.

A pruned file/key is reported by ``is_pruned`` and treated as already-visited
by ``SharedFrontier`` (core.explorer), so every scout drops the cluster from
its frontier immediately.

All mutating access is guarded by ``lock``; ``graph_lock`` separately
serialises tool calls that touch the (not thread-safe) GraphRetriever caches.
"""

from __future__ import annotations

import logging
import threading

from config import config

logger = logging.getLogger(__name__)


class SharedScoutBoard:
    """Thread-safe blackboard shared by parallel competitive scouts."""

    def __init__(self, tracker=None, hypotheses=None):
        self.lock = threading.Lock()
        # Serialises GraphRetriever access (its memo caches mutate on read)
        self.graph_lock = threading.Lock()
        self.tracker = tracker
        self.hypotheses = list(hypotheses or [])
        self.pruned_files: set[str] = set()
        self.pruned_keys: set[tuple[str, str]] = set()
        self._low_streak: dict[str, int] = {}
        # Observability: what got pruned and why (read after the run)
        self.prune_events: list[str] = []

    # ── read side (hot path, kept cheap) ─────────────────────────────────────

    @staticmethod
    def _target_file(target: str) -> str:
        return target.split("::", 1)[0] if "::" in target else target

    def is_pruned(self, key: tuple[str, str]) -> bool:
        """True if this (kind, target) — or its file — has been ruled out."""
        if not self.pruned_files and not self.pruned_keys:
            return False  # common case: nothing pruned yet, no lock needed
        with self.lock:
            if key in self.pruned_keys:
                return True
            return self._target_file(key[1]) in self.pruned_files

    # ── write side (guarded) ─────────────────────────────────────────────────

    def record_evidence(self, hid: str, evidence) -> list[str]:
        """
        Apply one evidence item to the shared tracker and report any hypothesis
        that transitioned to 'falsified' as a result of THIS update.
        """
        if self.tracker is None or not hid:
            return []
        with self.lock:
            h = self.tracker.get(hid)
            was_falsified = h is not None and h.status == "falsified"
            self.tracker.update(hid, evidence)
            newly: list[str] = []
            if h is not None and h.status == "falsified" and not was_falsified:
                newly.append(hid)
            return newly

    def prune_falsified(self, newly_falsified: list[str]) -> list[str]:
        """
        Prune files belonging ONLY to falsified hypotheses (mirrors
        HypothesisTracker.file_scores' negative-score rule). Files still claimed
        by any surviving hypothesis are kept. Returns the files newly pruned.
        """
        if not newly_falsified or self.tracker is None:
            return []
        with self.lock:
            surviving_files: set[str] = set()
            for h in self.tracker.surviving():
                for fp in h.suspected_files:
                    fp = (fp or "").strip()
                    if fp:
                        surviving_files.add(fp)
            pruned_now: list[str] = []
            for hid in newly_falsified:
                h = self.tracker.get(hid)
                if h is None:
                    continue
                for fp in h.suspected_files:
                    fp = (fp or "").strip()
                    if fp and fp not in surviving_files and fp not in self.pruned_files:
                        self.pruned_files.add(fp)
                        pruned_now.append(fp)
                        self.prune_events.append(f"{fp} (falsified {hid})")
            if pruned_now:
                logger.info(
                    f"[MACS] pruned {len(pruned_now)} file(s) from falsified "
                    f"{newly_falsified}: {pruned_now}"
                )
            return pruned_now

    def note_relevance(self, file_path: str, relevance: int) -> bool:
        """
        Track a per-file low-relevance streak. Prune the file once it has been
        scored low ``scouting_prune_low_streak`` times in a row. Returns True if
        this call pruned the file.
        """
        fp = (file_path or "").strip()
        if not fp:
            return False
        with self.lock:
            if fp in self.pruned_files:
                return False
            if relevance < config.scouting_prune_relevance:
                self._low_streak[fp] = self._low_streak.get(fp, 0) + 1
                if self._low_streak[fp] >= config.scouting_prune_low_streak:
                    self.pruned_files.add(fp)
                    self.prune_events.append(
                        f"{fp} (low relevance ×{self._low_streak[fp]})"
                    )
                    logger.info(
                        f"[MACS] pruned {fp} after "
                        f"{self._low_streak[fp]} low-relevance observations"
                    )
                    return True
            else:
                self._low_streak.pop(fp, None)
            return False

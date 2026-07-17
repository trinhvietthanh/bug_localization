"""Unit tests for P0 recall fixes: FileValidation non-empty guarantee and
run_search exclusion from navigation locations."""

from agents.orchestrator import Orchestrator
from agents.priority_navigation import PriorityNavigationAgent
from core.explorer import Observation


# ── FileValidation ───────────────────────────────────────────────────────────

def test_filter_keeps_existing_and_drops_missing(tmp_path):
    (tmp_path / "real.py").write_text("x = 1\n")
    out = Orchestrator._filter_nonexistent_files(
        ["real.py", "ghost.py"], str(tmp_path)
    )
    assert out == ["real.py"]


def test_filter_never_returns_empty_when_input_nonempty(tmp_path):
    out = Orchestrator._filter_nonexistent_files(
        ["ghost_a.py", "ghost_b.py", "ghost_a.py"], str(tmp_path)
    )
    # all unresolvable → originals kept (deduped), NOT []
    assert out == ["ghost_a.py", "ghost_b.py"]


def test_filter_empty_input_stays_empty(tmp_path):
    assert Orchestrator._filter_nonexistent_files([], str(tmp_path)) == []


# ── run_search exclusion ─────────────────────────────────────────────────────

def _obs(kind, entity, file_path, relevance=7):
    return Observation(
        entity=entity, kind=kind, relevance=relevance,
        reason="r", file_path=file_path, start_line=1, end_line=2,
    )


def test_findings_to_locations_skips_run_search():
    findings = [
        _obs("inspect_file", "pkg/a.py", "pkg/a.py"),
        _obs("run_search", "grep -n 'READ TERR' pkg/a.py",
             "grep -n 'READ TERR' pkg/a.py", relevance=9),
        _obs("inspect_function", "pkg/b.py::f", "pkg/b.py"),
    ]
    locs = PriorityNavigationAgent._findings_to_locations(findings)
    fps = [l["file_path"] for l in locs]
    assert fps == ["pkg/a.py", "pkg/b.py"]

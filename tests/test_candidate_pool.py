"""
Tests for Orchestrator candidate-pool building, path-keyword matching,
and nonexistent-path resolution (X.py → X/__init__.py).

These target the SWE-bench Lite failure modes found in results/swebench_50.json:
final ranked lists of only 1-3 files, empty lists on agent failure, and django
predictions like "fields.py" whose real location is "fields/__init__.py".
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import AgentContext
from agents.orchestrator import Orchestrator, LocalizationResult


@pytest.fixture
def fake_repo(tmp_path):
    """A miniature django-like repo layout."""
    files = [
        "django/db/models/fields/__init__.py",
        "django/db/models/query.py",
        "django/db/migrations/autodetector.py",
        "django/core/validators.py",
        "django/utils/autoreload.py",
        "django/contrib/auth/validators.py",
        "tests/test_models.py",
        "docs/conf.py",
    ]
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# stub\n")
    return str(tmp_path)


# ── _filter_nonexistent_files ─────────────────────────────────────────────────

def test_filter_resolves_module_to_package_init(fake_repo):
    out = Orchestrator._filter_nonexistent_files(
        ["django/db/models/fields.py"], fake_repo
    )
    assert out == ["django/db/models/fields/__init__.py"]


def test_filter_keeps_existing_and_drops_missing(fake_repo):
    out = Orchestrator._filter_nonexistent_files(
        ["django/db/models/query.py", "django/nonexistent/nope.py"], fake_repo
    )
    assert out == ["django/db/models/query.py"]


def test_filter_dedupes_after_resolution(fake_repo):
    out = Orchestrator._filter_nonexistent_files(
        ["django/db/models/fields.py", "django/db/models/fields/__init__.py"],
        fake_repo,
    )
    assert out == ["django/db/models/fields/__init__.py"]


# ── _path_keyword_candidates ──────────────────────────────────────────────────

def test_path_keywords_hit_basename(fake_repo):
    ctx = AgentContext(
        repo_path=fake_repo,
        keywords=["autodetector", "makemigrations"],
        file_extension="*.py",
    )
    hits = Orchestrator._path_keyword_candidates(ctx, top_k=5)
    assert "django/db/migrations/autodetector.py" in hits


def test_path_keywords_camelcase_split(fake_repo):
    ctx = AgentContext(
        repo_path=fake_repo,
        keywords=["ASCIIUsernameValidator"],
        file_extension="*.py",
    )
    hits = Orchestrator._path_keyword_candidates(ctx, top_k=5)
    # "validator(s)" subtoken should hit both validators.py files
    assert any(h.endswith("validators.py") for h in hits)


def test_path_keywords_skips_tests_and_docs(fake_repo):
    ctx = AgentContext(
        repo_path=fake_repo,
        keywords=["test_models", "conf"],
        file_extension="*.py",
    )
    hits = Orchestrator._path_keyword_candidates(ctx, top_k=10)
    assert all(not h.startswith(("tests/", "docs/")) for h in hits)


def test_path_keywords_empty_without_keywords(fake_repo):
    ctx = AgentContext(repo_path=fake_repo, keywords=[], file_extension="*.py")
    assert Orchestrator._path_keyword_candidates(ctx, top_k=5) == []


# ── _build_candidate_pool ─────────────────────────────────────────────────────

def _make_orchestrator():
    return Orchestrator()


def test_pool_priority_order(fake_repo):
    orch = _make_orchestrator()
    result = LocalizationResult(instance_id="t")
    result.ranked_locations = [
        {"file_path": "django/core/validators.py", "confidence": 0.9},
    ]
    ctx = AgentContext(
        repo_path=fake_repo,
        candidate_files=["django/db/models/query.py"],
        stack_trace_files=["django/utils/autoreload.py"],
        mentioned_files=["django/contrib/auth/validators.py"],
        keywords=[],
    )
    pool = orch._build_candidate_pool(result, ctx)
    assert pool[0] == "django/core/validators.py"          # confirmed first
    assert pool[1] == "django/db/models/query.py"          # then navigation
    assert "django/utils/autoreload.py" in pool            # then stack trace
    assert "django/contrib/auth/validators.py" in pool     # then mentioned


def test_pool_dedupes(fake_repo):
    orch = _make_orchestrator()
    result = LocalizationResult(instance_id="t")
    result.ranked_locations = [
        {"file_path": "django/db/models/query.py", "confidence": 0.9},
    ]
    ctx = AgentContext(
        repo_path=fake_repo,
        candidate_files=["django/db/models/query.py", "django/db/models/query.py"],
    )
    pool = orch._build_candidate_pool(result, ctx)
    assert pool.count("django/db/models/query.py") == 1


def test_pool_pads_from_path_keywords_when_thin(fake_repo):
    orch = _make_orchestrator()
    result = LocalizationResult(instance_id="t")
    result.ranked_locations = [
        {"file_path": "django/db/models/query.py", "confidence": 0.9},
    ]
    ctx = AgentContext(
        repo_path=fake_repo,
        candidate_files=[],
        keywords=["autodetector", "autoreload"],
        file_extension="*.py",
    )
    pool = orch._build_candidate_pool(result, ctx)
    assert pool[0] == "django/db/models/query.py"
    assert "django/db/migrations/autodetector.py" in pool
    assert "django/utils/autoreload.py" in pool


def test_pool_never_empty_when_any_source_has_files(fake_repo):
    """The 11422/12908 failure mode: agents produced nothing usable."""
    orch = _make_orchestrator()
    result = LocalizationResult(instance_id="t")  # no ranked_locations
    ctx = AgentContext(
        repo_path=fake_repo,
        candidate_files=[],
        keywords=["autoreload", "StatReloader"],
        file_extension="*.py",
    )
    pool = orch._build_candidate_pool(result, ctx)
    assert pool, "pool must not be empty when keywords match repo files"
    assert "django/utils/autoreload.py" in pool

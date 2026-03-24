"""
Tests for the Bug Localization Skill Python API (skill.py).

These tests verify the public interface without making any real LLM calls.
All network-dependent behaviour is mocked.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make thesis/ importable from anywhere
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_result(ranked_files=None, success=True):
    """Create a minimal mock LocalizationResult."""
    from agents.orchestrator import LocalizationResult

    r = LocalizationResult(instance_id="test_001")
    r.success = success
    r.ranked_files = ranked_files or ["src/core.py", "src/utils.py"]
    r.ranked_locations = [
        {
            "rank": 1,
            "file_path": "src/core.py",
            "function_name": "divide",
            "class_name": "Calculator",
            "confidence": 0.92,
            "explanation": "Operands are swapped in the divide method.",
        }
    ]
    r.root_cause = "The divide method has swapped operands (b/a instead of a/b)."
    r.explanation = "Detailed agent reasoning..."
    r.total_time = 5.1
    r.total_llm_calls = 6
    r.total_tool_calls = 14
    return r


def make_sample_repo(tmp_path: Path) -> Path:
    """Create a minimal Python repo in tmp_path."""
    repo = tmp_path / "sample_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "src" / "core.py").write_text(
        "class Calculator:\n"
        "    def divide(self, a, b):\n"
        "        return b / a  # bug: operands swapped\n"
    )
    return repo


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

class TestBugLocalizationSkillInit:
    def test_default_init(self):
        """Skill can be instantiated with defaults (no LLM calls)."""
        from skill import BugLocalizationSkill

        skill = BugLocalizationSkill()
        assert skill is not None
        assert skill._orchestrator is None  # lazy init

    def test_init_with_options(self):
        """Skill accepts enable_graph_rag and verbose overrides."""
        from skill import BugLocalizationSkill

        skill = BugLocalizationSkill(enable_graph_rag=False, verbose=True)
        from config import config
        assert config.enable_graph_rag is False
        assert skill._verbose is True

    def test_get_supported_benchmarks_returns_dict(self):
        """get_supported_benchmarks is a static method returning a dict."""
        from skill import BugLocalizationSkill

        benchmarks = BugLocalizationSkill.get_supported_benchmarks()
        assert isinstance(benchmarks, dict)
        assert "defects4j" in benchmarks
        assert "swe-bench" in benchmarks
        assert "bugsinpy" in benchmarks

    def test_benchmark_metadata_has_required_fields(self):
        """Each benchmark entry has required metadata fields."""
        from skill import BugLocalizationSkill

        benchmarks = BugLocalizationSkill.get_supported_benchmarks()
        for name, meta in benchmarks.items():
            assert "language" in meta, f"{name} missing 'language'"
            assert "requires_checkout" in meta, f"{name} missing 'requires_checkout'"


class TestLocalizeBug:
    @patch("skill.Orchestrator")
    def test_localize_bug_calls_orchestrator(self, MockOrchestrator, tmp_path):
        """localize_bug builds a BugInstance and calls Orchestrator.localize."""
        from skill import BugLocalizationSkill

        repo = make_sample_repo(tmp_path)
        mock_result = make_mock_result()
        mock_orch = MagicMock()
        mock_orch.localize.return_value = mock_result
        MockOrchestrator.return_value = mock_orch

        skill = BugLocalizationSkill(enable_graph_rag=False)
        result = skill.localize_bug(
            bug_report="Calculator.divide() has swapped operands.",
            repo_path=str(repo),
        )

        assert result.success is True
        assert "src/core.py" in result.ranked_files
        mock_orch.localize.assert_called_once()

    @patch("skill.Orchestrator")
    def test_localize_bug_result_to_dict(self, MockOrchestrator, tmp_path):
        """LocalizationResult.to_dict() returns JSON-serializable dict."""
        from skill import BugLocalizationSkill

        repo = make_sample_repo(tmp_path)
        mock_orch = MagicMock()
        mock_orch.localize.return_value = make_mock_result()
        MockOrchestrator.return_value = mock_orch

        skill = BugLocalizationSkill(enable_graph_rag=False)
        result = skill.localize_bug(
            bug_report="Some bug description.",
            repo_path=str(repo),
        )
        d = result.to_dict()

        # Must be JSON-serializable
        serialized = json.dumps(d)
        assert serialized  # non-empty

        # Check required keys
        assert "instance_id" in d
        assert "success" in d
        assert "ranked_files" in d
        assert "total_time" in d

    @patch("skill.Orchestrator")
    def test_localize_bug_with_language_java(self, MockOrchestrator, tmp_path):
        """Language 'java' sets repo field for detection heuristic."""
        from skill import BugLocalizationSkill

        repo = make_sample_repo(tmp_path)
        mock_orch = MagicMock()
        mock_orch.localize.return_value = make_mock_result()
        MockOrchestrator.return_value = mock_orch

        skill = BugLocalizationSkill(enable_graph_rag=False)
        result = skill.localize_bug(
            bug_report="NullPointerException in DateUtils.parse",
            repo_path=str(repo),
            language="java",
        )
        assert result is not None
        # Verify orchestrator was called
        mock_orch.localize.assert_called_once()


class TestIndexRepository:
    @patch("skill.BugLocalizationSkill._try_load_retriever", return_value=None)
    def test_index_repository_returns_stats(self, _mock_retriever, tmp_path):
        """index_repository returns a dict with num_chunks."""
        from skill import BugLocalizationSkill

        repo = make_sample_repo(tmp_path)
        skill = BugLocalizationSkill(enable_graph_rag=False)

        with patch("rag.indexer.CodebaseIndexer") as MockIndexer:
            mock_indexer = MagicMock()
            mock_indexer.index_repository.return_value = 42
            MockIndexer.return_value = mock_indexer

            stats = skill.index_repository(str(repo))

        assert stats["num_chunks"] == 42
        assert "collection" in stats
        assert "persist_dir" in stats


class TestMCPServer:
    def test_mcp_server_imports_without_error(self):
        """mcp_server.py imports successfully (mcp package may be mocked)."""
        mcp_mock = MagicMock()
        mcp_mock.server.fastmcp.FastMCP.return_value = MagicMock()

        with patch.dict("sys.modules", {
            "mcp": mcp_mock,
            "mcp.server": mcp_mock.server,
            "mcp.server.fastmcp": mcp_mock.server.fastmcp,
        }):
            import importlib
            import mcp_server as _old  # noqa: F401
            importlib.invalidate_caches()
            # Just ensure the module body runs without raising
            assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

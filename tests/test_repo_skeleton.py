"""Tests for repository skeleton generation."""

from tools.repo_skeleton import generate_repo_skeleton


def test_generate_repo_skeleton_for_python_repo():
    skeleton = generate_repo_skeleton(
        repo_path=".",
        language="python",
        max_files=20,
        max_entries_per_file=5,
    )
    assert isinstance(skeleton, str)
    assert skeleton

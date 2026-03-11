"""Unit tests for data loader."""

import pytest
from data.loader import BugInstance


class TestBugInstance:
    def test_extract_files_from_patch(self):
        patch = """diff --git a/src/utils.py b/src/utils.py
index abc123..def456 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -10,7 +10,7 @@
 def add(a, b):
-    return a - b
+    return a + b

diff --git a/src/main.py b/src/main.py
index 111222..333444 100644
--- a/src/main.py
+++ b/src/main.py
@@ -5,3 +5,3 @@
"""
        instance = BugInstance(
            instance_id="test-1",
            repo="test/repo",
            problem_statement="add is broken",
            base_commit="abc123",
            patch=patch,
            test_patch="",
        )
        assert instance.buggy_files == ["src/utils.py", "src/main.py"]

    def test_empty_patch(self):
        instance = BugInstance(
            instance_id="test-2",
            repo="test/repo",
            problem_statement="bug",
            base_commit="abc",
            patch="",
            test_patch="",
        )
        assert instance.buggy_files == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

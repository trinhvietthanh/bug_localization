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
        assert instance.buggy_methods == []

    def test_extract_methods_from_python_patch(self):
        """Python def context should yield bare method names."""
        patch = """diff --git a/foo.py b/foo.py
index 1..2 100644
--- a/foo.py
+++ b/foo.py
@@ -10,7 +10,7 @@ def add(a, b):
 def add(a, b):
-    return a - b
+    return a + b
@@ -50,7 +50,7 @@ def multiply(x, y):
 def multiply(x, y):
-    return x * y
+    return x + y
"""
        instance = BugInstance(
            instance_id="py-1",
            repo="r",
            problem_statement="ps",
            base_commit="c",
            patch=patch,
            test_patch="",
        )
        assert instance.buggy_methods == ["add", "multiply"]

    def test_extract_methods_from_python_class_method_patch(self):
        """Python def Class.method context should yield Class#method."""
        patch = """diff --git a/foo.py b/foo.py
@@ -10,7 +10,7 @@ def Calculator.add(self, a, b):
 def Calculator.add(self, a, b):
-        return a - b
+        return a + b
"""
        instance = BugInstance(
            instance_id="py-2",
            repo="r",
            problem_statement="ps",
            base_commit="c",
            patch=patch,
            test_patch="",
        )
        assert instance.buggy_methods == ["Calculator#add"]

    def test_extract_methods_from_java_patch(self):
        """Java ctags context: pkg.Class,method(args) → Class#method."""
        patch = """diff --git a/Foo.java b/Foo.java
@@ -10,7 +10,7 @@ org.apache.commons.Foo,bar(int)
 public int bar(int x) {
-        return x;
+        return x + 1;
"""
        instance = BugInstance(
            instance_id="java-1",
            repo="r",
            problem_statement="ps",
            base_commit="c",
            patch=patch,
            test_patch="",
        )
        assert instance.buggy_methods == ["Foo#bar"]

    def test_extract_methods_dedupes_across_hunks(self):
        """Repeated method context should only appear once."""
        patch = """@@ -1,1 +1,1 @@ def foo():
@@ -10,1 +10,1 @@ def foo():
@@ -20,1 +20,1 @@ def bar():
"""
        instance = BugInstance(
            instance_id="dup-1",
            repo="r",
            problem_statement="ps",
            base_commit="c",
            patch=patch,
            test_patch="",
        )
        assert instance.buggy_methods == ["foo", "bar"]

    def test_extract_methods_no_context(self):
        """Hunk headers with no context should yield no methods."""
        patch = """diff --git a/x.py b/x.py
@@ -1,1 +1,1 @@
-old
+new
"""
        instance = BugInstance(
            instance_id="noctx-1",
            repo="r",
            problem_statement="ps",
            base_commit="c",
            patch=patch,
            test_patch="",
        )
        assert instance.buggy_methods == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

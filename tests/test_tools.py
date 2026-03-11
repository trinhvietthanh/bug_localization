"""Unit tests for tools."""

import os
import pytest
import tempfile
from pathlib import Path

from tools.code_search import code_search, find_files
from tools.file_reader import read_file, list_directory
from tools.ast_parser import parse_python_file, get_file_outline, get_function_source


@pytest.fixture
def sample_repo(tmp_path):
    """Create a sample repository for testing."""
    # Create some Python files
    src = tmp_path / "src"
    src.mkdir()

    (src / "__init__.py").write_text("")

    (src / "utils.py").write_text('''
def add(a, b):
    """Add two numbers."""
    return a + b

def multiply(a, b):
    """Multiply two numbers."""
    return a * b

class Calculator:
    """A simple calculator."""

    def __init__(self):
        self.history = []

    def calculate(self, op, a, b):
        if op == "add":
            result = add(a, b)
        elif op == "multiply":
            result = multiply(a, b)
        else:
            raise ValueError(f"Unknown op: {op}")
        self.history.append(result)
        return result
''')

    (src / "main.py").write_text('''
from src.utils import Calculator

def main():
    calc = Calculator()
    print(calc.calculate("add", 1, 2))

if __name__ == "__main__":
    main()
''')

    return tmp_path


class TestCodeSearch:
    def test_basic_search(self, sample_repo):
        results = code_search("Calculator", str(sample_repo))
        assert len(results) > 0
        assert any("Calculator" in r.line_content for r in results)

    def test_search_with_file_pattern(self, sample_repo):
        results = code_search("def", str(sample_repo), file_pattern="*.py")
        assert len(results) > 0

    def test_no_results(self, sample_repo):
        results = code_search("nonexistent_xyz_123", str(sample_repo))
        assert len(results) == 0


class TestFileReader:
    def test_read_file(self, sample_repo):
        content = read_file("src/utils.py", str(sample_repo))
        assert "def add" in content
        assert "Calculator" in content

    def test_read_with_lines(self, sample_repo):
        content = read_file("src/utils.py", str(sample_repo), start_line=2, end_line=4)
        assert "add" in content

    def test_read_nonexistent(self, sample_repo):
        content = read_file("nonexistent.py", str(sample_repo))
        assert "Error" in content

    def test_list_directory(self, sample_repo):
        tree = list_directory("src", str(sample_repo))
        assert "utils.py" in tree
        assert "main.py" in tree


class TestASTParser:
    def test_parse_file(self, sample_repo):
        structure = parse_python_file("src/utils.py", str(sample_repo))
        assert len(structure.functions) >= 2
        assert len(structure.classes) >= 1
        assert structure.classes[0].name == "Calculator"

    def test_file_outline(self, sample_repo):
        outline = get_file_outline("src/utils.py", str(sample_repo))
        assert "Calculator" in outline
        assert "add" in outline
        assert "multiply" in outline

    def test_get_function_source(self, sample_repo):
        source = get_function_source("src/utils.py", str(sample_repo), "add")
        assert "def add" in source
        assert "return a + b" in source

    def test_get_method_source(self, sample_repo):
        source = get_function_source(
            "src/utils.py", str(sample_repo),
            "calculate", class_name="Calculator"
        )
        assert "def calculate" in source


class TestFindFiles:
    def test_find_python_files(self, sample_repo):
        files = find_files(str(sample_repo), extensions=[".py"])
        assert len(files) >= 3  # __init__.py, utils.py, main.py

    def test_find_by_pattern(self, sample_repo):
        files = find_files(str(sample_repo), pattern="utils")
        assert any("utils" in f for f in files)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Repository skeleton generator for lightweight structural context.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path


JAVA_CLASS_PATTERN = re.compile(r"\b(?:class|interface|enum)\s+([A-Za-z_]\w*)")
JAVA_METHOD_PATTERN = re.compile(
    r"\b(?:public|protected|private|static|final|synchronized|native|abstract|\s)+"
    r"[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\(",
)


def generate_repo_skeleton(
    repo_path: str,
    language: str,
    max_files: int = 200,
    max_entries_per_file: int = 8,
    priority_hints: list[str] | None = None,
) -> str:
    """
    Build compact skeleton lines:
    relative/path.py: [Class(m1,m2), function_a, ...]
    """
    root = Path(repo_path)
    extension = ".java" if language.lower() == "java" else ".py"
    lines: list[str] = []
    hints = tuple(
        sorted({h.strip().lower() for h in (priority_hints or []) if h and str(h).strip()})
    )
    candidate_paths = _collect_candidate_paths_cached(str(root), extension, hints, max_files)

    for rel_path in candidate_paths:
        file_path = root / rel_path
        entries = (
            _extract_python_entries(file_path)
            if extension == ".py"
            else _extract_java_entries(file_path)
        )
        if not entries:
            continue
        compact = ", ".join(entries[:max_entries_per_file])
        lines.append(f"{rel_path.as_posix()}: [{compact}]")

    if not lines:
        return "No parsable source skeleton found."
    return "\n".join(lines)


def skeleton_for_files(
    repo_path: str,
    files: list[str],
    language: str,
    max_entries_per_file: int = 10,
) -> dict[str, str]:
    """
    Compact per-file skeletons for a specific file list (not a repo walk).

    Returns {relative_path: "Class(m1,m2), function_a, ..."}; files that are
    missing or unparsable map to an empty string so callers can degrade.
    """
    root = Path(repo_path)
    is_java = language.lower() == "java"
    skeletons: dict[str, str] = {}
    for rel in files:
        file_path = root / rel
        if not file_path.is_file():
            skeletons[rel] = ""
            continue
        entries = (
            _extract_java_entries(file_path)
            if is_java
            else _extract_python_entries(file_path)
        )
        skeletons[rel] = ", ".join(entries[:max_entries_per_file])
    return skeletons


def _extract_python_entries(file_path: Path) -> list[str]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except Exception:
        return []

    classes: list[str] = []
    functions: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            method_part = ",".join(methods[:5])
            classes.append(f"{node.name}({method_part})" if method_part else node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
    return classes + functions[:8]


def _extract_java_entries(file_path: Path) -> list[str]:
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    classes = JAVA_CLASS_PATTERN.findall(source)
    methods = [
        m for m in JAVA_METHOD_PATTERN.findall(source)
        if m not in {"if", "for", "while", "switch", "catch", "return", "new"}
    ]
    class_entries = classes[:4]
    method_entries = methods[:10]
    if class_entries and method_entries:
        return [f"{class_entries[0]}({','.join(method_entries[:5])})"] + class_entries[1:]
    return class_entries + method_entries


@lru_cache(maxsize=64)
def _collect_candidate_paths_cached(
    root_str: str,
    extension: str,
    hints: tuple[str, ...],
    max_files: int,
) -> tuple[Path, ...]:
    """Cache file discovery/sorting across repeated runs on the same repo."""
    root = Path(root_str)
    candidates = [
        p.relative_to(root)
        for p in root.rglob(f"*{extension}")
        if ".git" not in p.parts and "venv" not in p.parts and "__pycache__" not in p.parts
    ]
    if hints:
        def _hint_score(path: Path) -> int:
            s = path.as_posix().lower()
            return sum(1 for h in hints if h in s)

        candidates.sort(key=lambda p: (-_hint_score(p), p.as_posix()))
    else:
        candidates.sort(key=lambda p: p.as_posix())
    return tuple(candidates[:max_files])

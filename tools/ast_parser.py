"""
AST parser tool for agents.
Extracts structural information from Python source files.
"""

import ast
import logging
from pathlib import Path
from dataclasses import dataclass, field

from tools.cache import read_file_cached, parse_ast_cached

logger = logging.getLogger(__name__)


@dataclass
class FunctionInfo:
    """Information about a function/method."""
    name: str
    qualified_name: str  # e.g., ClassName.method_name
    start_line: int
    end_line: int
    args: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    docstring: str = ""
    is_method: bool = False
    class_name: str = ""


@dataclass
class ClassInfo:
    """Information about a class."""
    name: str
    start_line: int
    end_line: int
    bases: list[str] = field(default_factory=list)
    methods: list[FunctionInfo] = field(default_factory=list)
    docstring: str = ""


@dataclass
class FileStructure:
    """Structural information about a Python file."""
    file_path: str
    imports: list[str] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    global_variables: list[str] = field(default_factory=list)


def parse_python_file(file_path: str, repo_path: str) -> FileStructure:
    """
    Parse a Python file and extract its structure.

    Args:
        file_path: Relative path to the Python file
        repo_path: Path to the repository root

    Returns:
        FileStructure with classes, functions, imports
    """
    full_path = Path(repo_path) / file_path

    if not full_path.exists():
        logger.error(f"File not found: {file_path}")
        return FileStructure(file_path=file_path)

    full_path_str = str(full_path)
    tree = parse_ast_cached(full_path_str)
    if tree is None:
        logger.warning(f"Syntax error in {file_path}")
        return FileStructure(file_path=file_path)

    structure = FileStructure(file_path=file_path)

    # Process top-level nodes
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            structure.imports.append(_format_import(node))

        elif isinstance(node, ast.ClassDef):
            class_info = _parse_class(node)
            structure.classes.append(class_info)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_info = _parse_function(node)
            structure.functions.append(func_info)

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    structure.global_variables.append(target.id)

    return structure


def _parse_class(node: ast.ClassDef) -> ClassInfo:
    """Parse a class definition."""
    bases = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute):
            bases.append(ast.unparse(base))

    class_info = ClassInfo(
        name=node.name,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        bases=bases,
        docstring=ast.get_docstring(node) or "",
    )

    # Parse methods
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = _parse_function(child, class_name=node.name)
            class_info.methods.append(method)

    return class_info


def _parse_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_name: str = "",
) -> FunctionInfo:
    """Parse a function/method definition."""
    args = []
    for arg in node.args.args:
        args.append(arg.arg)

    decorators = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            decorators.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            decorators.append(ast.unparse(dec))
        elif isinstance(dec, ast.Call):
            decorators.append(ast.unparse(dec))

    qualified_name = f"{class_name}.{node.name}" if class_name else node.name

    return FunctionInfo(
        name=node.name,
        qualified_name=qualified_name,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        args=args,
        decorators=decorators,
        docstring=ast.get_docstring(node) or "",
        is_method=bool(class_name),
        class_name=class_name,
    )


def _format_import(node) -> str:
    """Format an import statement."""
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
        return f"import {', '.join(names)}"
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        names = [alias.name for alias in node.names]
        return f"from {module} import {', '.join(names)}"
    return ""


def get_file_outline(file_path: str, repo_path: str) -> str:
    """
    Get a human-readable outline of a Python file's structure.

    Returns a formatted string showing classes, methods, and functions.
    """
    structure = parse_python_file(file_path, repo_path)

    lines = [f"=== File Outline: {file_path} ===\n"]

    if structure.imports:
        lines.append("Imports:")
        for imp in structure.imports[:10]:
            lines.append(f"  {imp}")
        if len(structure.imports) > 10:
            lines.append(f"  ... and {len(structure.imports) - 10} more")
        lines.append("")

    for cls in structure.classes:
        bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
        lines.append(f"class {cls.name}{bases_str}:  [L{cls.start_line}-{cls.end_line}]")
        if cls.docstring:
            lines.append(f"  \"\"\"{cls.docstring[:100]}...\"\"\"")
        for method in cls.methods:
            args_str = ", ".join(method.args)
            deco = f"@{method.decorators[0]} " if method.decorators else ""
            lines.append(f"  {deco}def {method.name}({args_str})  [L{method.start_line}-{method.end_line}]")
        lines.append("")

    for func in structure.functions:
        args_str = ", ".join(func.args)
        deco = f"@{func.decorators[0]} " if func.decorators else ""
        lines.append(f"{deco}def {func.name}({args_str})  [L{func.start_line}-{func.end_line}]")

    return "\n".join(lines)


def get_function_source(
    file_path: str,
    repo_path: str,
    function_name: str,
    class_name: str = None,
) -> str:
    """
    Extract the source code of a specific function/method.

    Args:
        file_path: Relative path to the file
        repo_path: Repo root
        function_name: Name of the function
        class_name: Optional class name if looking for a method

    Returns:
        Source code of the function with line numbers
    """
    structure = parse_python_file(file_path, repo_path)

    target = None

    if class_name:
        for cls in structure.classes:
            if cls.name == class_name:
                for method in cls.methods:
                    if method.name == function_name:
                        target = method
                        break
    else:
        for func in structure.functions:
            if func.name == function_name:
                target = func
                break

    if not target:
        return f"Function '{function_name}' not found in {file_path}"

    # Read source lines from cache (avoids redundant I/O after parse)
    full_path = Path(repo_path) / file_path
    lines = read_file_cached(str(full_path)).split("\n")
    func_lines = lines[target.start_line - 1:target.end_line]

    numbered = []
    for i, line in enumerate(func_lines, start=target.start_line):
        numbered.append(f"{i:4d} | {line}")

    return f"# {target.qualified_name} [{file_path}]\n" + "\n".join(numbered)


# Tool description for LLM agents
TOOL_DESCRIPTION = {
    "name": "get_file_outline",
    "description": (
        "Get a structural outline of a Python file showing its classes, methods, "
        "functions, and imports with line numbers. Use this to understand the "
        "high-level structure of a file before diving into specific sections."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to the Python file"
            }
        },
        "required": ["file_path"]
    }
}

GET_FUNCTION_TOOL_DESCRIPTION = {
    "name": "get_function_source",
    "description": (
        "Get the full source code of a specific function or method. "
        "Provide the file path and function name. For methods, also provide the class name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Relative path to the Python file"
            },
            "function_name": {
                "type": "string",
                "description": "Name of the function or method"
            },
            "class_name": {
                "type": "string",
                "description": "Class name if looking for a method (optional)"
            }
        },
        "required": ["file_path", "function_name"]
    }
}

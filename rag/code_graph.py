"""
Code Property Graph (CPG) for Graph RAG.
Builds a graph of code relationships: files, classes, functions,
and their interconnections (calls, imports, inheritance).

Supports Python (via AST) and Java (via regex-based heuristics).

Uses the Adapter Pattern: CodePropertyGraph is the in-memory backend
that implements the abstract GraphBackend interface.
"""

import ast
import os
import re
import logging
from pathlib import Path
from collections import defaultdict

from rag.graph_backend import GraphBackend, GraphNode, GraphEdge  # noqa: F401

logger = logging.getLogger(__name__)


# ──────────────────────────── In-Memory Backend ─────────────────────

class CodePropertyGraph(GraphBackend):
    """
    In-memory Code Property Graph (implements GraphBackend).

    Nodes represent code elements (files, classes, functions).
    Edges represent relationships (calls, imports, inheritance).

    This is the default backend. For persistent storage, use Neo4jGraph.
    """

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        # Adjacency lists for fast traversal
        self._outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        self._incoming: dict[str, list[GraphEdge]] = defaultdict(list)
        # Index: name -> list of node IDs (for fuzzy lookup)
        self._name_index: dict[str, list[str]] = defaultdict(list)

    # ─── Node/Edge Management ───

    def add_node(self, node: GraphNode):
        """Add a node to the graph."""
        self.nodes[node.id] = node
        self._name_index[node.name.lower()].append(node.id)

    def add_edge(self, edge: GraphEdge):
        """Add an edge to the graph."""
        self.edges.append(edge)
        self._outgoing[edge.source_id].append(edge)
        self._incoming[edge.target_id].append(edge)

    # ─── Query Methods ───

    def get_node(self, node_id: str) -> GraphNode | None:
        """Get a node by its ID."""
        return self.nodes.get(node_id)

    def find_nodes_by_name(self, name: str) -> list[GraphNode]:
        """Find all nodes matching a name (case-insensitive)."""
        ids = self._name_index.get(name.lower(), [])
        return [self.nodes[nid] for nid in ids if nid in self.nodes]

    def find_nodes_by_type(self, node_type: str) -> list[GraphNode]:
        """Find all nodes of a given type."""
        return [n for n in self.nodes.values() if n.node_type == node_type]

    def get_callees(self, node_id: str) -> list[GraphNode]:
        """Get all nodes that the given node calls."""
        return self._get_neighbors(node_id, "CALLS", direction="out")

    def get_callers(self, node_id: str) -> list[GraphNode]:
        """Get all nodes that call the given node."""
        return self._get_neighbors(node_id, "CALLS", direction="in")

    def get_imports(self, node_id: str) -> list[GraphNode]:
        """Get all nodes imported by the given node."""
        return self._get_neighbors(node_id, "IMPORTS", direction="out")

    def get_children(self, node_id: str) -> list[GraphNode]:
        """Get contained elements (e.g., methods of a class)."""
        return self._get_neighbors(node_id, "CONTAINS", direction="out")

    def get_parent(self, node_id: str) -> GraphNode | None:
        """Get the parent (e.g., class containing a method)."""
        parents = self._get_neighbors(node_id, "CONTAINS", direction="in")
        return parents[0] if parents else None

    def get_superclasses(self, node_id: str) -> list[GraphNode]:
        """Get parent classes (inheritance)."""
        return self._get_neighbors(node_id, "INHERITS", direction="out")

    def get_subclasses(self, node_id: str) -> list[GraphNode]:
        """Get child classes (inheritance)."""
        return self._get_neighbors(node_id, "INHERITS", direction="in")

    def get_neighbors(
        self, node_id: str, max_depth: int = 2
    ) -> list[tuple[GraphNode, int, str]]:
        """
        BFS traversal: get all neighbors up to max_depth.

        Returns:
            List of (node, depth, edge_type) tuples.
        """
        visited = {node_id}
        result = []
        queue = [(node_id, 0)]

        while queue:
            current_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue

            # Check both outgoing and incoming edges
            for edge in self._outgoing.get(current_id, []):
                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    target = self.nodes.get(edge.target_id)
                    if target:
                        result.append((target, depth + 1, edge.edge_type))
                        queue.append((edge.target_id, depth + 1))

            for edge in self._incoming.get(current_id, []):
                if edge.source_id not in visited:
                    visited.add(edge.source_id)
                    source = self.nodes.get(edge.source_id)
                    if source:
                        result.append((source, depth + 1, edge.edge_type))
                        queue.append((edge.source_id, depth + 1))

        return result

    def _get_neighbors(
        self, node_id: str, edge_type: str, direction: str
    ) -> list[GraphNode]:
        """Get neighbors filtered by edge type and direction."""
        result = []
        if direction == "out":
            for edge in self._outgoing.get(node_id, []):
                if edge.edge_type == edge_type:
                    node = self.nodes.get(edge.target_id)
                    if node:
                        result.append(node)
        else:  # "in"
            for edge in self._incoming.get(node_id, []):
                if edge.edge_type == edge_type:
                    node = self.nodes.get(edge.source_id)
                    if node:
                        result.append(node)
        return result

    # ─── Write Operations ───

    def clear(self) -> None:
        """Remove all nodes and edges."""
        self.nodes.clear()
        self.edges.clear()
        self._outgoing.clear()
        self._incoming.clear()
        self._name_index.clear()

    # ─── Iteration ───

    def all_nodes(self) -> list[GraphNode]:
        """Return all nodes."""
        return list(self.nodes.values())

    def all_edges(self) -> list[GraphEdge]:
        """Return all edges."""
        return list(self.edges)

    # ─── Statistics ───

    def stats(self) -> dict:
        """Return graph statistics."""
        type_counts = defaultdict(int)
        for n in self.nodes.values():
            type_counts[n.node_type] += 1

        edge_type_counts = defaultdict(int)
        for e in self.edges:
            edge_type_counts[e.edge_type] += 1

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": dict(type_counts),
            "edge_types": dict(edge_type_counts),
        }


# ──────────────────────────── Graph Builders ────────────────────────

class PythonGraphBuilder:
    """Builds a Code Property Graph from Python source files using AST."""

    def build(self, repo_path: str, extensions: set[str] = None) -> CodePropertyGraph:
        """Build graph from all Python files in a repository."""
        if extensions is None:
            extensions = {".py"}

        graph = CodePropertyGraph()
        repo = Path(repo_path)

        skip_dirs = {
            ".git", "__pycache__", "node_modules", ".tox",
            "build", "dist", ".venv", "venv", ".eggs",
        }

        py_files: list[Path] = []
        # Collect files with os.walk (faster than repo.rglob("*") on large repos)
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip_dirs and "test" not in d.lower()]
            root_path = Path(root)
            for filename in files:
                path = root_path / filename
                if path.suffix in extensions:
                    py_files.append(path)
        py_files.sort()

        logger.info(f"Building Python graph from {len(py_files)} files")

        for filepath in py_files:
            rel_path = str(filepath.relative_to(repo))
            try:
                source = filepath.read_text(encoding="utf-8", errors="ignore")
                self._process_file(graph, source, rel_path)
            except Exception as e:
                logger.debug(f"Failed to parse {rel_path}: {e}")

        # Second pass: resolve call edges
        self._resolve_calls(graph, repo)

        logger.info(
            f"Graph built: {graph.stats()}"
        )
        return graph

    def _process_file(
        self, graph: CodePropertyGraph, source: str, file_path: str
    ):
        """Process a single Python file and add nodes/edges."""
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return

        # File node
        file_id = f"file::{file_path}"
        graph.add_node(GraphNode(
            id=file_id,
            name=Path(file_path).stem,
            node_type="file",
            file_path=file_path,
        ))

        # Process imports
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target_id = f"module::{alias.name}"
                    graph.add_node(GraphNode(
                        id=target_id,
                        name=alias.name,
                        node_type="module",
                        file_path="",
                    ))
                    graph.add_edge(GraphEdge(
                        source_id=file_id,
                        target_id=target_id,
                        edge_type="IMPORTS",
                    ))

            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for alias in node.names:
                    full_name = f"{module_name}.{alias.name}" if module_name else alias.name
                    target_id = f"symbol::{full_name}"
                    graph.add_node(GraphNode(
                        id=target_id,
                        name=alias.name,
                        node_type="symbol",
                        file_path="",
                    ))
                    graph.add_edge(GraphEdge(
                        source_id=file_id,
                        target_id=target_id,
                        edge_type="IMPORTS",
                    ))

            elif isinstance(node, ast.ClassDef):
                self._process_class(graph, node, file_path, file_id)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_function(graph, node, file_path, file_id)

    def _process_class(
        self, graph: CodePropertyGraph, node: ast.ClassDef,
        file_path: str, parent_id: str,
    ):
        """Process a class definition."""
        class_id = f"class::{file_path}::{node.name}"
        graph.add_node(GraphNode(
            id=class_id,
            name=node.name,
            node_type="class",
            file_path=file_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            signature=f"class {node.name}",
            docstring=ast.get_docstring(node) or "",
        ))

        # CONTAINS edge: file -> class
        graph.add_edge(GraphEdge(
            source_id=parent_id,
            target_id=class_id,
            edge_type="CONTAINS",
        ))

        # Inheritance edges
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = ast.unparse(base)
            else:
                continue
            base_id = f"class::*::{base_name}"  # Placeholder for resolution
            graph.add_edge(GraphEdge(
                source_id=class_id,
                target_id=base_id,
                edge_type="INHERITS",
            ))

        # Process methods
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_function(
                    graph, child, file_path, class_id, class_name=node.name
                )

    def _process_function(
        self, graph: CodePropertyGraph, node, file_path: str,
        parent_id: str, class_name: str = "",
    ):
        """Process a function/method definition."""
        if class_name:
            func_id = f"method::{file_path}::{class_name}.{node.name}"
            node_type = "method"
        else:
            func_id = f"function::{file_path}::{node.name}"
            node_type = "function"

        args = [a.arg for a in node.args.args]
        signature = f"def {node.name}({', '.join(args)})"

        graph.add_node(GraphNode(
            id=func_id,
            name=node.name,
            node_type=node_type,
            file_path=file_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            signature=signature,
            docstring=ast.get_docstring(node) or "",
        ))

        # CONTAINS edge: parent -> function
        graph.add_edge(GraphEdge(
            source_id=parent_id,
            target_id=func_id,
            edge_type="CONTAINS",
        ))

    def _resolve_calls(self, graph: CodePropertyGraph, repo: Path):
        """
        Second pass: scan function bodies for call expressions
        and add CALLS edges.

        Uses file-scoped resolution to avoid combinatorial explosion:
        - Calls are resolved to functions in the SAME file first.
        - If no same-file match, falls back to functions in imported modules.
        - Max edges per function is capped to prevent degenerate cases.
        """
        MAX_EDGES_PER_FUNC = 10

        func_nodes = [
            n for n in graph.nodes.values()
            if n.node_type in ("function", "method")
        ]

        # Build file_path -> {name -> [node_ids]} for file-scoped resolution
        file_func_map: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for n in func_nodes:
            file_func_map[n.file_path][n.name].append(n.id)

        # Build global name -> [node_ids] as fallback (but limited usage)
        global_func_map: dict[str, list[str]] = defaultdict(list)
        for n in func_nodes:
            global_func_map[n.name].append(n.id)

        # Build file -> set of imported symbol names (for cross-file resolution)
        file_imports: dict[str, set[str]] = defaultdict(set)
        for edge in graph.edges:
            if edge.edge_type == "IMPORTS":
                src_node = graph.get_node(edge.source_id)
                tgt_node = graph.get_node(edge.target_id)
                if src_node and tgt_node and src_node.file_path:
                    file_imports[src_node.file_path].add(tgt_node.name)

        # Cache parsed ASTs: file_path -> ast tree (parse each file only once)
        ast_cache: dict[str, ast.AST | None] = {}

        def _get_ast(filepath: Path) -> ast.AST | None:
            key = str(filepath)
            if key not in ast_cache:
                try:
                    source = filepath.read_text(encoding="utf-8", errors="ignore")
                    ast_cache[key] = ast.parse(source)
                except Exception:
                    ast_cache[key] = None
            return ast_cache[key]

        for func_node in func_nodes:
            filepath = repo / func_node.file_path
            tree = _get_ast(filepath)
            if tree is None:
                continue

            # Find the AST node for this function
            edges_added = 0
            for ast_node in ast.walk(tree):
                if not isinstance(ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if ast_node.lineno != func_node.start_line:
                    continue

                # Scan body for Call expressions
                for child in ast.walk(ast_node):
                    if edges_added >= MAX_EDGES_PER_FUNC:
                        break
                    if isinstance(child, ast.Call):
                        callee_name = self._extract_call_name(child)
                        if not callee_name:
                            continue

                        # 1) Same-file resolution (preferred)
                        same_file_targets = file_func_map.get(
                            func_node.file_path, {}
                        ).get(callee_name, [])

                        targets = [
                            t for t in same_file_targets if t != func_node.id
                        ]

                        # 2) Imported-symbol fallback: only if callee name
                        #    appears in this file's imports
                        if not targets:
                            imported_names = file_imports.get(
                                func_node.file_path, set()
                            )
                            if callee_name in imported_names:
                                global_targets = global_func_map.get(
                                    callee_name, []
                                )
                                targets = [
                                    t for t in global_targets
                                    if t != func_node.id
                                ][:3]  # Limit cross-file matches

                        for target_id in targets:
                            if edges_added >= MAX_EDGES_PER_FUNC:
                                break
                            graph.add_edge(GraphEdge(
                                source_id=func_node.id,
                                target_id=target_id,
                                edge_type="CALLS",
                            ))
                            edges_added += 1
                break  # Found our function, no need to continue

    @staticmethod
    def _extract_call_name(call_node: ast.Call) -> str:
        """Extract the function name from a Call AST node."""
        func = call_node.func
        if isinstance(func, ast.Name):
            return func.id
        elif isinstance(func, ast.Attribute):
            return func.attr
        return ""


class JavaGraphBuilder:
    """
    Builds a Code Property Graph from Java source files.
    Uses regex-based heuristics (no full Java parser needed).
    """

    # Regex patterns for Java
    CLASS_RE = re.compile(
        r"^\s*(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?"
        r"(?:class|interface|enum)\s+(\w+)"
        r"(?:\s+extends\s+(\w+))?"
        r"(?:\s+implements\s+([\w,\s]+))?",
        re.MULTILINE,
    )
    METHOD_RE = re.compile(
        r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?"
        r"(?:synchronized\s+)?(?:abstract\s+)?"
        r"(?:[\w<>\[\],\s]+?)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
        re.MULTILINE,
    )
    IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+);", re.MULTILINE)
    CALL_RE = re.compile(r"\b(\w+)\s*\(", re.MULTILINE)

    def build(self, repo_path: str, extensions: set[str] = None) -> CodePropertyGraph:
        """Build graph from all Java files in a repository."""
        if extensions is None:
            extensions = {".java"}

        graph = CodePropertyGraph()
        repo = Path(repo_path)

        skip_dirs = {
            ".git", "build", "target", ".gradle", "node_modules",
            "test", "tests", ".idea",
        }

        java_files: list[Path] = []
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip_dirs and "test" not in d.lower()]
            root_path = Path(root)
            for filename in files:
                path = root_path / filename
                if path.suffix in extensions:
                    java_files.append(path)
        java_files.sort()

        logger.info(f"Building Java graph from {len(java_files)} files")

        for filepath in java_files:
            rel_path = str(filepath.relative_to(repo))
            try:
                source = filepath.read_text(encoding="utf-8", errors="ignore")
                self._process_java_file(graph, source, rel_path)
            except Exception as e:
                logger.debug(f"Failed to parse {rel_path}: {e}")

        logger.info(f"Graph built: {graph.stats()}")
        return graph

    def _process_java_file(
        self, graph: CodePropertyGraph, source: str, file_path: str
    ):
        """Process a single Java file."""
        # File node
        file_id = f"file::{file_path}"
        graph.add_node(GraphNode(
            id=file_id,
            name=Path(file_path).stem,
            node_type="file",
            file_path=file_path,
        ))

        lines = source.split("\n")

        # Imports
        for match in self.IMPORT_RE.finditer(source):
            import_path = match.group(1)
            import_id = f"import::{import_path}"
            graph.add_node(GraphNode(
                id=import_id,
                name=import_path.split(".")[-1],
                node_type="import",
                file_path="",
            ))
            graph.add_edge(GraphEdge(
                source_id=file_id,
                target_id=import_id,
                edge_type="IMPORTS",
            ))

        # Classes
        class_positions = []
        class_starts = []
        for match in self.CLASS_RE.finditer(source):
            class_name = match.group(1)
            extends = match.group(2)
            implements = match.group(3)

            class_id = f"class::{file_path}::{class_name}"
            line_no = source[:match.start()].count("\n") + 1

            graph.add_node(GraphNode(
                id=class_id,
                name=class_name,
                node_type="class",
                file_path=file_path,
                start_line=line_no,
                signature=f"class {class_name}",
            ))

            graph.add_edge(GraphEdge(
                source_id=file_id,
                target_id=class_id,
                edge_type="CONTAINS",
            ))

            # Inheritance
            if extends:
                graph.add_edge(GraphEdge(
                    source_id=class_id,
                    target_id=f"class::*::{extends.strip()}",
                    edge_type="INHERITS",
                ))
            if implements:
                for iface in implements.split(","):
                    iface = iface.strip()
                    if iface:
                        graph.add_edge(GraphEdge(
                            source_id=class_id,
                            target_id=f"class::*::{iface}",
                            edge_type="INHERITS",
                        ))
                        
            class_positions.append((match.start(), class_name))
            class_starts.append(match.start())

        import bisect

        # Methods
        for match in self.METHOD_RE.finditer(source):
            method_name = match.group(1)
            params = match.group(2).strip()
            line_no = source[:match.start()].count("\n") + 1

            # Find containing class using precomputed positions (O(log C) instead of O(N^2))
            idx = bisect.bisect_right(class_starts, match.start())
            containing_class = class_positions[idx - 1][1] if idx > 0 else ""
            
            if containing_class:
                method_id = f"method::{file_path}::{containing_class}.{method_name}@{line_no}"
                parent_id = f"class::{file_path}::{containing_class}"
            else:
                method_id = f"function::{file_path}::{method_name}@{line_no}"
                parent_id = file_id

            graph.add_node(GraphNode(
                id=method_id,
                name=method_name,
                node_type="method" if containing_class else "function",
                file_path=file_path,
                start_line=line_no,
                signature=f"{method_name}({params})",
            ))

            graph.add_edge(GraphEdge(
                source_id=parent_id,
                target_id=method_id,
                edge_type="CONTAINS",
            ))

            # Find calls within method body
            method_body = self._extract_method_body(source, match.end())
            if method_body:
                for call_match in self.CALL_RE.finditer(method_body):
                    callee = call_match.group(1)
                    # Skip Java keywords and common patterns
                    if callee in ("if", "for", "while", "switch", "catch",
                                  "return", "new", "throw", "super", "this"):
                        continue
                    callee_id = f"method::*::*.{callee}"
                    graph.add_edge(GraphEdge(
                        source_id=method_id,
                        target_id=callee_id,
                        edge_type="CALLS",
                    ))

    def _extract_method_body(self, source: str, start_pos: int) -> str:
        """
        Extract method body by counting braces, skipping string/char literals
        and line/block comments so that braces inside them are not counted.
        """
        depth = 1
        i = start_pos
        n = len(source)
        while i < n and depth > 0:
            ch = source[i]
            if ch == '"':
                # Skip string literal — handles escaped quotes (\")
                i += 1
                while i < n:
                    if source[i] == '\\':
                        i += 2
                        continue
                    if source[i] == '"':
                        break
                    i += 1
            elif ch == "'":
                # Skip char literal — handles escaped chars (\')
                i += 1
                while i < n:
                    if source[i] == '\\':
                        i += 2
                        continue
                    if source[i] == "'":
                        break
                    i += 1
            elif ch == '/' and i + 1 < n:
                if source[i + 1] == '/':
                    # Line comment: skip to end of line
                    while i < n and source[i] != '\n':
                        i += 1
                    continue
                elif source[i + 1] == '*':
                    # Block comment: skip to */
                    i += 2
                    while i + 1 < n:
                        if source[i] == '*' and source[i + 1] == '/':
                            i += 1
                            break
                        i += 1
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            i += 1
        return source[start_pos:i] if depth == 0 else ""


# ──────────────────────────── Factory ────────────────────────────

def _detect_language(repo_path: str) -> str:
    """Auto-detect dominant language in a repository."""
    repo = Path(repo_path)
    py_count = 0
    java_count = 0
    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".tox", ".eggs",
        "build", "dist", ".venv", "venv", ".mypy_cache", ".pytest_cache",
        "target", ".gradle", ".idea",
    }
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for filename in files:
            if filename.endswith(".py"):
                py_count += 1
            elif filename.endswith(".java"):
                java_count += 1
    language = "java" if java_count > py_count else "python"
    logger.info(f"Auto-detected language: {language} (py={py_count}, java={java_count})")
    return language


def build_code_graph(repo_path: str, language: str = "auto") -> CodePropertyGraph:
    """
    Build a Code Property Graph for a repository (in-memory).

    Args:
        repo_path: Path to the repository
        language: "python", "java", or "auto" (detect from file types)

    Returns:
        CodePropertyGraph instance
    """
    if language == "auto":
        language = _detect_language(repo_path)

    if language == "java":
        builder = JavaGraphBuilder()
        return builder.build(repo_path, extensions={".java"})
    else:
        builder = PythonGraphBuilder()
        return builder.build(repo_path, extensions={".py"})


def get_or_build_graph(
    repo_path: str,
    language: str = "auto",
    use_neo4j: bool = False,
    neo4j_uri: str = "bolt://localhost:7687",
    neo4j_user: str = "neo4j",
    neo4j_password: str = "password",
    neo4j_database: str = "neo4j",
):
    """
    Get a graph backend — either in-memory or Neo4j.

    When Neo4j is enabled, checks if a graph already exists in the database.
    If it does (and has nodes), returns the existing Neo4j graph directly
    without rebuilding. Otherwise builds from source and imports.

    Args:
        repo_path: Path to the repository
        language: "python", "java", or "auto"
        use_neo4j: Whether to use Neo4j backend
        neo4j_uri: Neo4j Bolt URI
        neo4j_user: Neo4j username
        neo4j_password: Neo4j password
        neo4j_database: Neo4j database name

    Returns:
        GraphBackend instance (CodePropertyGraph or Neo4jGraph)
    """
    if not use_neo4j:
        return build_code_graph(repo_path, language=language)

    from rag.neo4j_backend import Neo4jGraph

    neo4j_graph = Neo4jGraph(
        uri=neo4j_uri, user=neo4j_user,
        password=neo4j_password, database=neo4j_database,
    )

    # Check if Neo4j already has data
    stats = neo4j_graph.stats()
    if stats["total_nodes"] > 0:
        logger.info(
            f"Using existing Neo4j graph: {stats['total_nodes']} nodes, "
            f"{stats['total_edges']} edges"
        )
        return neo4j_graph

    # Build from source and import
    logger.info("Neo4j graph empty — building from source and importing...")
    in_memory = build_code_graph(repo_path, language=language)
    neo4j_graph.import_from_in_memory(in_memory)
    return neo4j_graph

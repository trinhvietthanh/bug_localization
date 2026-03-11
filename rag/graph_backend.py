"""
Abstract interface for Code Property Graph backends.

This module defines the contract that all graph backends must implement,
enabling the Adapter Pattern: swap between In-Memory and Neo4j backends
without changing the rest of the system.

Usage:
    from rag.graph_backend import GraphBackend
    # Use InMemoryGraph or Neo4jGraph — same interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ──────────────────────────── Data Model ────────────────────────────
# Shared across all backends.

@dataclass
class GraphNode:
    """A node in the Code Property Graph."""
    id: str                    # Unique ID: "file::class::method"
    name: str                  # Short name: "processPayment"
    node_type: str             # "file", "class", "function", "method"
    file_path: str             # Relative path to source file
    start_line: int = 0
    end_line: int = 0
    signature: str = ""        # e.g., "def processPayment(amount, currency)"
    docstring: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    """A directed edge in the Code Property Graph."""
    source_id: str
    target_id: str
    edge_type: str             # "CALLS", "IMPORTS", "INHERITS", "CONTAINS"
    metadata: dict = field(default_factory=dict)


# ──────────────────────────── Abstract Backend ────────────────────────

class GraphBackend(ABC):
    """
    Abstract interface for Code Property Graph storage.

    All backends (InMemory, Neo4j, etc.) must implement this interface.
    This enables the Adapter Pattern: the rest of the system only depends
    on this interface, not on a specific storage technology.
    """

    # ─── Write Operations ───

    @abstractmethod
    def add_node(self, node: GraphNode) -> None:
        """Add a node to the graph."""
        ...

    @abstractmethod
    def add_edge(self, edge: GraphEdge) -> None:
        """Add a directed edge to the graph."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all nodes and edges."""
        ...

    # ─── Read Operations ───

    @abstractmethod
    def get_node(self, node_id: str) -> GraphNode | None:
        """Get a node by its unique ID."""
        ...

    @abstractmethod
    def find_nodes_by_name(self, name: str) -> list[GraphNode]:
        """Find all nodes matching a name (case-insensitive)."""
        ...

    @abstractmethod
    def find_nodes_by_type(self, node_type: str) -> list[GraphNode]:
        """Find all nodes of a given type."""
        ...

    # ─── Relationship Queries ───

    @abstractmethod
    def get_callees(self, node_id: str) -> list[GraphNode]:
        """Get functions/methods called by this node."""
        ...

    @abstractmethod
    def get_callers(self, node_id: str) -> list[GraphNode]:
        """Get functions/methods that call this node."""
        ...

    @abstractmethod
    def get_imports(self, node_id: str) -> list[GraphNode]:
        """Get modules/symbols imported by this node."""
        ...

    @abstractmethod
    def get_children(self, node_id: str) -> list[GraphNode]:
        """Get contained elements (e.g., methods of a class)."""
        ...

    @abstractmethod
    def get_parent(self, node_id: str) -> GraphNode | None:
        """Get the parent element (e.g., class containing a method)."""
        ...

    @abstractmethod
    def get_superclasses(self, node_id: str) -> list[GraphNode]:
        """Get parent classes (inheritance)."""
        ...

    @abstractmethod
    def get_subclasses(self, node_id: str) -> list[GraphNode]:
        """Get child classes (inheritance)."""
        ...

    # ─── Traversal ───

    @abstractmethod
    def get_neighbors(
        self, node_id: str, max_depth: int = 2
    ) -> list[tuple[GraphNode, int, str]]:
        """
        BFS traversal from a node.

        Returns:
            List of (node, depth, edge_type) tuples.
        """
        ...

    # ─── Statistics ───

    @abstractmethod
    def stats(self) -> dict:
        """Return graph statistics (node/edge counts by type)."""
        ...

    # ─── Iteration ───

    @abstractmethod
    def all_nodes(self) -> list[GraphNode]:
        """Return all nodes in the graph."""
        ...

    @abstractmethod
    def all_edges(self) -> list[GraphEdge]:
        """Return all edges in the graph."""
        ...

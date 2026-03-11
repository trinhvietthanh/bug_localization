"""Tests for the Code Property Graph and Graph RAG retriever."""

import pytest
from rag.code_graph import CodePropertyGraph, GraphNode, GraphEdge, PythonGraphBuilder


class TestCodePropertyGraph:
    """Test basic graph operations."""

    def _build_sample_graph(self) -> CodePropertyGraph:
        """Build a small sample graph for testing."""
        g = CodePropertyGraph()

        # File
        g.add_node(GraphNode(
            id="file::app.py", name="app", node_type="file",
            file_path="app.py",
        ))

        # Class
        g.add_node(GraphNode(
            id="class::app.py::PaymentService", name="PaymentService",
            node_type="class", file_path="app.py",
            start_line=10, end_line=50,
        ))
        g.add_edge(GraphEdge(
            source_id="file::app.py",
            target_id="class::app.py::PaymentService",
            edge_type="CONTAINS",
        ))

        # Methods
        g.add_node(GraphNode(
            id="method::app.py::PaymentService.process",
            name="process", node_type="method", file_path="app.py",
            start_line=15, end_line=30,
            signature="def process(self, amount)",
        ))
        g.add_edge(GraphEdge(
            source_id="class::app.py::PaymentService",
            target_id="method::app.py::PaymentService.process",
            edge_type="CONTAINS",
        ))

        g.add_node(GraphNode(
            id="method::app.py::PaymentService.validate",
            name="validate", node_type="method", file_path="app.py",
            start_line=32, end_line=40,
            signature="def validate(self, data)",
        ))
        g.add_edge(GraphEdge(
            source_id="class::app.py::PaymentService",
            target_id="method::app.py::PaymentService.validate",
            edge_type="CONTAINS",
        ))

        # Another file with a utility function
        g.add_node(GraphNode(
            id="file::utils.py", name="utils", node_type="file",
            file_path="utils.py",
        ))
        g.add_node(GraphNode(
            id="function::utils.py::calculate_tax",
            name="calculate_tax", node_type="function", file_path="utils.py",
            start_line=5, end_line=15,
            signature="def calculate_tax(amount, rate)",
        ))
        g.add_edge(GraphEdge(
            source_id="file::utils.py",
            target_id="function::utils.py::calculate_tax",
            edge_type="CONTAINS",
        ))

        # CALLS relationship: process -> calculate_tax
        g.add_edge(GraphEdge(
            source_id="method::app.py::PaymentService.process",
            target_id="function::utils.py::calculate_tax",
            edge_type="CALLS",
        ))

        # CALLS relationship: process -> validate
        g.add_edge(GraphEdge(
            source_id="method::app.py::PaymentService.process",
            target_id="method::app.py::PaymentService.validate",
            edge_type="CALLS",
        ))

        return g

    def test_node_lookup(self):
        g = self._build_sample_graph()
        node = g.get_node("class::app.py::PaymentService")
        assert node is not None
        assert node.name == "PaymentService"

    def test_find_by_name(self):
        g = self._build_sample_graph()
        nodes = g.find_nodes_by_name("process")
        assert len(nodes) == 1
        assert nodes[0].name == "process"

    def test_callees(self):
        g = self._build_sample_graph()
        callees = g.get_callees("method::app.py::PaymentService.process")
        names = {c.name for c in callees}
        assert "calculate_tax" in names
        assert "validate" in names

    def test_callers(self):
        g = self._build_sample_graph()
        callers = g.get_callers("function::utils.py::calculate_tax")
        assert len(callers) == 1
        assert callers[0].name == "process"

    def test_children(self):
        g = self._build_sample_graph()
        children = g.get_children("class::app.py::PaymentService")
        names = {c.name for c in children}
        assert "process" in names
        assert "validate" in names

    def test_parent(self):
        g = self._build_sample_graph()
        parent = g.get_parent("method::app.py::PaymentService.process")
        assert parent is not None
        assert parent.name == "PaymentService"

    def test_bfs_neighbors(self):
        g = self._build_sample_graph()
        neighbors = g.get_neighbors(
            "method::app.py::PaymentService.process", max_depth=2
        )
        names = {n.name for n, _, _ in neighbors}
        # Should find: validate, calculate_tax (depth 1),
        # PaymentService (depth 1, via CONTAINS incoming),
        # possibly utils/app file nodes
        assert "validate" in names
        assert "calculate_tax" in names

    def test_stats(self):
        g = self._build_sample_graph()
        stats = g.stats()
        assert stats["total_nodes"] > 0
        assert stats["total_edges"] > 0
        assert "CALLS" in stats["edge_types"]
        assert "CONTAINS" in stats["edge_types"]


class TestPythonGraphBuilder:
    """Test Python AST-based graph building."""

    def test_build_from_source(self, tmp_path):
        """Build graph from a temporary Python file."""
        # Create a sample Python file
        sample = tmp_path / "sample.py"
        sample.write_text("""
import os
from pathlib import Path

class Calculator:
    def add(self, a, b):
        return a + b

    def multiply(self, a, b):
        return a * b

def helper(x):
    c = Calculator()
    return c.add(x, 1)
""")

        builder = PythonGraphBuilder()
        graph = builder.build(str(tmp_path), extensions={".py"})

        # Check nodes were created
        assert graph.stats()["total_nodes"] > 0

        # Find the Calculator class
        calcs = graph.find_nodes_by_name("Calculator")
        assert len(calcs) == 1
        assert calcs[0].node_type == "class"

        # Find methods
        adds = graph.find_nodes_by_name("add")
        assert len(adds) >= 1

        helpers = graph.find_nodes_by_name("helper")
        assert len(helpers) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

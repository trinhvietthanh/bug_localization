"""Tests for Neo4j backend (Adapter Pattern)."""

import pytest
from rag.graph_backend import GraphBackend, GraphNode, GraphEdge


def _neo4j_available():
    """Check if Neo4j server is reachable."""
    try:
        from rag.neo4j_backend import Neo4jGraph
        graph = Neo4jGraph(uri="bolt://localhost:7687", password="password")
        graph.close()
        return True
    except Exception:
        return False


# Skip all tests in this module if Neo4j is not available
pytestmark = pytest.mark.skipif(
    not _neo4j_available(),
    reason="Neo4j server not available at bolt://localhost:7687"
)


class TestNeo4jBackend:
    """Test Neo4j backend conforms to GraphBackend interface."""

    @pytest.fixture
    def graph(self):
        """Create and clean a Neo4j graph for each test."""
        from rag.neo4j_backend import Neo4jGraph
        g = Neo4jGraph(uri="bolt://localhost:7687", password="password")
        g.clear()
        yield g
        g.clear()
        g.close()

    def _populate(self, g):
        """Add sample data to graph."""
        g.add_node(GraphNode(
            id="file::app.py", name="app", node_type="file", file_path="app.py"
        ))
        g.add_node(GraphNode(
            id="class::app.py::Service", name="Service",
            node_type="class", file_path="app.py", start_line=10, end_line=50,
        ))
        g.add_node(GraphNode(
            id="method::app.py::Service.run", name="run",
            node_type="method", file_path="app.py", start_line=15, end_line=30,
            signature="def run(self)",
        ))
        g.add_node(GraphNode(
            id="function::utils.py::helper", name="helper",
            node_type="function", file_path="utils.py", start_line=5, end_line=15,
        ))

        g.add_edge(GraphEdge(
            source_id="file::app.py",
            target_id="class::app.py::Service",
            edge_type="CONTAINS",
        ))
        g.add_edge(GraphEdge(
            source_id="class::app.py::Service",
            target_id="method::app.py::Service.run",
            edge_type="CONTAINS",
        ))
        g.add_edge(GraphEdge(
            source_id="method::app.py::Service.run",
            target_id="function::utils.py::helper",
            edge_type="CALLS",
        ))

    def test_is_graph_backend(self, graph):
        """Neo4jGraph implements GraphBackend."""
        assert isinstance(graph, GraphBackend)

    def test_add_and_get_node(self, graph):
        graph.add_node(GraphNode(
            id="file::test.py", name="test", node_type="file", file_path="test.py"
        ))
        node = graph.get_node("file::test.py")
        assert node is not None
        assert node.name == "test"
        assert node.node_type == "file"

    def test_find_by_name(self, graph):
        self._populate(graph)
        nodes = graph.find_nodes_by_name("run")
        assert len(nodes) == 1
        assert nodes[0].name == "run"

    def test_callees(self, graph):
        self._populate(graph)
        callees = graph.get_callees("method::app.py::Service.run")
        assert len(callees) == 1
        assert callees[0].name == "helper"

    def test_callers(self, graph):
        self._populate(graph)
        callers = graph.get_callers("function::utils.py::helper")
        assert len(callers) == 1
        assert callers[0].name == "run"

    def test_children(self, graph):
        self._populate(graph)
        children = graph.get_children("class::app.py::Service")
        assert len(children) == 1
        assert children[0].name == "run"

    def test_parent(self, graph):
        self._populate(graph)
        parent = graph.get_parent("method::app.py::Service.run")
        assert parent is not None
        assert parent.name == "Service"

    def test_stats(self, graph):
        self._populate(graph)
        stats = graph.stats()
        assert stats["total_nodes"] == 4
        assert stats["total_edges"] == 3
        assert stats["backend"] == "neo4j"

    def test_batch_insert(self, graph):
        """Batch insert is significantly faster."""
        nodes = [
            GraphNode(id=f"node::{i}", name=f"node_{i}", node_type="method", file_path="test.py")
            for i in range(100)
        ]
        graph.add_nodes_batch(nodes)
        stats = graph.stats()
        assert stats["total_nodes"] == 100

    def test_cypher_query(self, graph):
        """Test raw Cypher queries (Neo4j-specific)."""
        self._populate(graph)
        results = graph.cypher(
            "MATCH (a:CodeNode)-[:CALLS]->(b:CodeNode) RETURN a.name AS caller, b.name AS callee"
        )
        assert len(results) == 1
        assert results[0]["caller"] == "run"
        assert results[0]["callee"] == "helper"

    def test_import_from_in_memory(self, graph):
        """Test importing from InMemory graph to Neo4j."""
        from rag.code_graph import CodePropertyGraph

        # Build in-memory graph
        mem = CodePropertyGraph()
        mem.add_node(GraphNode(id="n1", name="Alpha", node_type="class", file_path="a.py"))
        mem.add_node(GraphNode(id="n2", name="Beta", node_type="class", file_path="b.py"))
        mem.add_edge(GraphEdge(source_id="n1", target_id="n2", edge_type="INHERITS"))

        # Import to Neo4j
        graph.import_from_in_memory(mem)

        # Verify
        stats = graph.stats()
        assert stats["total_nodes"] == 2
        assert stats["total_edges"] == 1

        superclasses = graph.get_superclasses("n1")
        assert len(superclasses) == 1
        assert superclasses[0].name == "Beta"

    def test_clear(self, graph):
        self._populate(graph)
        assert graph.stats()["total_nodes"] > 0
        graph.clear()
        assert graph.stats()["total_nodes"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

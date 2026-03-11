"""
Neo4j Backend for Code Property Graph.

Implements the GraphBackend interface using Neo4j as the storage engine.
Provides persistent storage, Cypher queries, and native graph visualization.

Requirements:
    - pip install neo4j
    - Neo4j server running (local or Docker)

Usage:
    from rag.neo4j_backend import Neo4jGraph

    graph = Neo4jGraph(uri="bolt://localhost:7687", user="neo4j", password="password")
    graph.add_node(GraphNode(...))
    graph.add_edge(GraphEdge(...))

    # Use Cypher for advanced queries
    results = graph.cypher("MATCH (a)-[:CALLS]->(b) RETURN a.name, b.name LIMIT 10")
"""

import logging
from collections import defaultdict

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

from rag.graph_backend import GraphBackend, GraphNode, GraphEdge

logger = logging.getLogger(__name__)


class Neo4jGraph(GraphBackend):
    """
    Neo4j-backed Code Property Graph (implements GraphBackend).

    Advantages over InMemory:
    - Persistent storage (survives restarts)
    - Native Cypher query language for complex graph patterns
    - Neo4j Browser for interactive visualization
    - Scales to millions of nodes

    Compatible with the same interface as CodePropertyGraph (InMemory).
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
        database: str = "neo4j",
    ):
        """
        Connect to a Neo4j instance.

        Args:
            uri: Bolt URI (e.g., "bolt://localhost:7687")
            user: Neo4j username
            password: Neo4j password
            database: Database name
        """
        self.uri = uri
        self.user = user
        self.database = database
        self._driver = None

        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
            # Verify connectivity
            self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j at {uri}")
        except ServiceUnavailable:
            raise ConnectionError(
                f"Cannot connect to Neo4j at {uri}. "
                f"Please ensure Neo4j is running.\n"
                f"Quick start with Docker:\n"
                f"  docker run -d --name neo4j -p 7474:7474 -p 7687:7687 "
                f"-e NEO4J_AUTH=neo4j/password neo4j:latest"
            )
        except AuthError:
            raise ConnectionError(
                f"Authentication failed for Neo4j at {uri}. "
                f"Check username/password."
            )

        # Create constraints/indexes for performance
        self._setup_indexes()

    def close(self):
        """Close the Neo4j driver connection."""
        if self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed")

    def __del__(self):
        self.close()

    # ─── Setup ───

    def _setup_indexes(self):
        """Create indexes for efficient lookups."""
        queries = [
            "CREATE INDEX node_id IF NOT EXISTS FOR (n:CodeNode) ON (n.node_id)",
            "CREATE INDEX node_name IF NOT EXISTS FOR (n:CodeNode) ON (n.name)",
            "CREATE INDEX node_type IF NOT EXISTS FOR (n:CodeNode) ON (n.node_type)",
        ]
        with self._driver.session(database=self.database) as session:
            for q in queries:
                try:
                    session.run(q)
                except Exception as e:
                    logger.debug(f"Index creation note: {e}")

    # ─── Write Operations ───

    def add_node(self, node: GraphNode) -> None:
        """Add a node to Neo4j."""
        query = """
        MERGE (n:CodeNode {node_id: $node_id})
        SET n.name = $name,
            n.node_type = $node_type,
            n.file_path = $file_path,
            n.start_line = $start_line,
            n.end_line = $end_line,
            n.signature = $signature,
            n.docstring = $docstring
        """
        # Also add a label based on node_type for easier Cypher queries
        label_query = f"""
        MATCH (n:CodeNode {{node_id: $node_id}})
        SET n:{self._safe_label(node.node_type)}
        """

        with self._driver.session(database=self.database) as session:
            session.run(query, {
                "node_id": node.id,
                "name": node.name,
                "node_type": node.node_type,
                "file_path": node.file_path,
                "start_line": node.start_line,
                "end_line": node.end_line,
                "signature": node.signature,
                "docstring": node.docstring[:500] if node.docstring else "",
            })
            try:
                session.run(label_query, {"node_id": node.id})
            except Exception:
                pass  # Label might have invalid characters

    def add_edge(self, edge: GraphEdge) -> None:
        """Add a directed edge to Neo4j."""
        # Use MERGE to avoid duplicate edges
        query = f"""
        MATCH (a:CodeNode {{node_id: $source_id}})
        MATCH (b:CodeNode {{node_id: $target_id}})
        MERGE (a)-[r:{edge.edge_type}]->(b)
        """
        with self._driver.session(database=self.database) as session:
            session.run(query, {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
            })

    def add_nodes_batch(self, nodes: list[GraphNode]) -> None:
        """Add multiple nodes in a single transaction (much faster)."""
        query = """
        UNWIND $nodes AS node
        MERGE (n:CodeNode {node_id: node.node_id})
        SET n.name = node.name,
            n.node_type = node.node_type,
            n.file_path = node.file_path,
            n.start_line = node.start_line,
            n.end_line = node.end_line,
            n.signature = node.signature,
            n.docstring = node.docstring
        """
        batch = [
            {
                "node_id": n.id,
                "name": n.name,
                "node_type": n.node_type,
                "file_path": n.file_path,
                "start_line": n.start_line,
                "end_line": n.end_line,
                "signature": n.signature,
                "docstring": (n.docstring[:500] if n.docstring else ""),
            }
            for n in nodes
        ]
        with self._driver.session(database=self.database) as session:
            session.run(query, {"nodes": batch})
        logger.debug(f"Batch inserted {len(batch)} nodes")

    def add_edges_batch(self, edges: list[GraphEdge]) -> None:
        """Add multiple edges in a single transaction (much faster)."""
        # Group edges by type (Neo4j requires relationship type in query)
        by_type: dict[str, list[dict]] = defaultdict(list)
        for e in edges:
            by_type[e.edge_type].append({
                "source_id": e.source_id,
                "target_id": e.target_id,
            })

        with self._driver.session(database=self.database) as session:
            for edge_type, edge_batch in by_type.items():
                query = f"""
                UNWIND $edges AS edge
                MATCH (a:CodeNode {{node_id: edge.source_id}})
                MATCH (b:CodeNode {{node_id: edge.target_id}})
                MERGE (a)-[:{edge_type}]->(b)
                """
                session.run(query, {"edges": edge_batch})
                logger.debug(f"Batch inserted {len(edge_batch)} {edge_type} edges")

    def clear(self) -> None:
        """Remove all nodes and edges from the database."""
        with self._driver.session(database=self.database) as session:
            session.run("MATCH (n:CodeNode) DETACH DELETE n")
        logger.info("Neo4j graph cleared")

    # ─── Read Operations ───

    def get_node(self, node_id: str) -> GraphNode | None:
        """Get a node by its unique ID."""
        query = "MATCH (n:CodeNode {node_id: $node_id}) RETURN n"
        with self._driver.session(database=self.database) as session:
            result = session.run(query, {"node_id": node_id})
            record = result.single()
            if record:
                return self._record_to_node(record["n"])
        return None

    def find_nodes_by_name(self, name: str) -> list[GraphNode]:
        """Find all nodes matching a name (case-insensitive)."""
        query = """
        MATCH (n:CodeNode)
        WHERE toLower(n.name) = toLower($name)
        RETURN n
        """
        return self._query_nodes(query, {"name": name})

    def find_nodes_by_type(self, node_type: str) -> list[GraphNode]:
        """Find all nodes of a given type."""
        query = "MATCH (n:CodeNode {node_type: $node_type}) RETURN n"
        return self._query_nodes(query, {"node_type": node_type})

    # ─── Relationship Queries ───

    def get_callees(self, node_id: str) -> list[GraphNode]:
        """Get functions/methods called by this node."""
        query = """
        MATCH (a:CodeNode {node_id: $node_id})-[:CALLS]->(b:CodeNode)
        RETURN b
        """
        return self._query_nodes(query, {"node_id": node_id}, key="b")

    def get_callers(self, node_id: str) -> list[GraphNode]:
        """Get functions/methods that call this node."""
        query = """
        MATCH (b:CodeNode)-[:CALLS]->(a:CodeNode {node_id: $node_id})
        RETURN b
        """
        return self._query_nodes(query, {"node_id": node_id}, key="b")

    def get_imports(self, node_id: str) -> list[GraphNode]:
        """Get modules/symbols imported by this node."""
        query = """
        MATCH (a:CodeNode {node_id: $node_id})-[:IMPORTS]->(b:CodeNode)
        RETURN b
        """
        return self._query_nodes(query, {"node_id": node_id}, key="b")

    def get_children(self, node_id: str) -> list[GraphNode]:
        """Get contained elements."""
        query = """
        MATCH (a:CodeNode {node_id: $node_id})-[:CONTAINS]->(b:CodeNode)
        RETURN b
        """
        return self._query_nodes(query, {"node_id": node_id}, key="b")

    def get_parent(self, node_id: str) -> GraphNode | None:
        """Get the parent element."""
        query = """
        MATCH (b:CodeNode)-[:CONTAINS]->(a:CodeNode {node_id: $node_id})
        RETURN b LIMIT 1
        """
        results = self._query_nodes(query, {"node_id": node_id}, key="b")
        return results[0] if results else None

    def get_superclasses(self, node_id: str) -> list[GraphNode]:
        """Get parent classes (inheritance)."""
        query = """
        MATCH (a:CodeNode {node_id: $node_id})-[:INHERITS]->(b:CodeNode)
        RETURN b
        """
        return self._query_nodes(query, {"node_id": node_id}, key="b")

    def get_subclasses(self, node_id: str) -> list[GraphNode]:
        """Get child classes (inheritance)."""
        query = """
        MATCH (b:CodeNode)-[:INHERITS]->(a:CodeNode {node_id: $node_id})
        RETURN b
        """
        return self._query_nodes(query, {"node_id": node_id}, key="b")

    # ─── Traversal ───

    def get_neighbors(
        self, node_id: str, max_depth: int = 2
    ) -> list[tuple[GraphNode, int, str]]:
        """BFS traversal using Cypher variable-length paths."""
        query = f"""
        MATCH path = (start:CodeNode {{node_id: $node_id}})-[r*1..{max_depth}]-(neighbor:CodeNode)
        WHERE neighbor.node_id <> $node_id
        WITH neighbor, length(path) AS depth, type(r[0]) AS edge_type
        RETURN DISTINCT neighbor, depth, edge_type
        ORDER BY depth
        """
        results = []
        with self._driver.session(database=self.database) as session:
            records = session.run(query, {"node_id": node_id})
            for record in records:
                node = self._record_to_node(record["neighbor"])
                depth = record["depth"]
                edge_type = record["edge_type"]
                results.append((node, depth, edge_type))
        return results

    # ─── Statistics ───

    def stats(self) -> dict:
        """Return graph statistics."""
        with self._driver.session(database=self.database) as session:
            # Node counts by type
            node_result = session.run("""
                MATCH (n:CodeNode)
                RETURN n.node_type AS type, count(*) AS count
            """)
            node_types = {}
            total_nodes = 0
            for record in node_result:
                node_types[record["type"]] = record["count"]
                total_nodes += record["count"]

            # Edge counts by type
            edge_result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) AS type, count(*) AS count
            """)
            edge_types = {}
            total_edges = 0
            for record in edge_result:
                edge_types[record["type"]] = record["count"]
                total_edges += record["count"]

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "node_types": node_types,
            "edge_types": edge_types,
            "backend": "neo4j",
        }

    # ─── Iteration ───

    def all_nodes(self) -> list[GraphNode]:
        """Return all nodes in the graph."""
        query = "MATCH (n:CodeNode) RETURN n"
        return self._query_nodes(query)

    def all_edges(self) -> list[GraphEdge]:
        """Return all edges in the graph."""
        query = """
        MATCH (a:CodeNode)-[r]->(b:CodeNode)
        RETURN a.node_id AS source, b.node_id AS target, type(r) AS edge_type
        """
        edges = []
        with self._driver.session(database=self.database) as session:
            records = session.run(query)
            for record in records:
                edges.append(GraphEdge(
                    source_id=record["source"],
                    target_id=record["target"],
                    edge_type=record["edge_type"],
                ))
        return edges

    # ─── Cypher (Neo4j-specific) ───

    def cypher(self, query: str, params: dict = None) -> list[dict]:
        """
        Execute a raw Cypher query (Neo4j-specific feature).

        This is the main advantage of Neo4j over in-memory graphs:
        you can express complex graph patterns declaratively.

        Examples:
            # Find all methods that call createNumber directly or indirectly
            graph.cypher('''
                MATCH path = (a:CodeNode)-[:CALLS*1..3]->(b:CodeNode {name: 'createNumber'})
                RETURN a.name, length(path) as distance
                ORDER BY distance
            ''')

            # Find classes with the most methods
            graph.cypher('''
                MATCH (c:CodeNode {node_type: 'class'})-[:CONTAINS]->(m:CodeNode {node_type: 'method'})
                RETURN c.name, count(m) as method_count
                ORDER BY method_count DESC LIMIT 10
            ''')
        """
        with self._driver.session(database=self.database) as session:
            result = session.run(query, params or {})
            return [dict(record) for record in result]

    # ─── Import from InMemory ───

    def import_from_in_memory(self, in_memory_graph) -> None:
        """
        Import all nodes and edges from an in-memory CodePropertyGraph.

        This enables the workflow:
        1. Build graph fast in-memory
        2. Export to Neo4j for persistence and visualization

        Args:
            in_memory_graph: A CodePropertyGraph (in-memory backend)
        """
        logger.info("Importing graph to Neo4j...")

        # Clear existing data
        self.clear()

        # Batch insert nodes
        all_nodes = in_memory_graph.all_nodes()
        batch_size = 500
        for i in range(0, len(all_nodes), batch_size):
            batch = all_nodes[i:i + batch_size]
            self.add_nodes_batch(batch)
            logger.debug(f"Imported nodes {i}-{i + len(batch)}")

        # Batch insert edges
        all_edges = in_memory_graph.all_edges()
        for i in range(0, len(all_edges), batch_size):
            batch = all_edges[i:i + batch_size]
            self.add_edges_batch(batch)
            logger.debug(f"Imported edges {i}-{i + len(batch)}")

        stats = self.stats()
        logger.info(
            f"Neo4j import complete: "
            f"{stats['total_nodes']} nodes, {stats['total_edges']} edges"
        )

    # ─── Private Helpers ───

    def _query_nodes(
        self, query: str, params: dict = None, key: str = "n"
    ) -> list[GraphNode]:
        """Execute a query and return nodes."""
        nodes = []
        with self._driver.session(database=self.database) as session:
            records = session.run(query, params or {})
            for record in records:
                nodes.append(self._record_to_node(record[key]))
        return nodes

    @staticmethod
    def _record_to_node(record) -> GraphNode:
        """Convert a Neo4j record to a GraphNode."""
        return GraphNode(
            id=record.get("node_id", ""),
            name=record.get("name", ""),
            node_type=record.get("node_type", ""),
            file_path=record.get("file_path", ""),
            start_line=record.get("start_line", 0),
            end_line=record.get("end_line", 0),
            signature=record.get("signature", ""),
            docstring=record.get("docstring", ""),
        )

    @staticmethod
    def _safe_label(label: str) -> str:
        """Convert a string to a valid Neo4j label."""
        return label.replace(" ", "_").replace("-", "_").capitalize()

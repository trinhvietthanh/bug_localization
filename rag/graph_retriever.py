"""
Graph-enhanced Retriever (Graph RAG).
Combines vector-based semantic search with Code Property Graph traversal
to retrieve structurally-related code context.

Workflow:
1. Vector Search → Find "anchor" nodes (semantically similar to query)
2. Graph Traversal → Expand from anchors to related nodes (callers, callees, etc.)
3. Context Assembly → Combine and rank all retrieved code for the LLM
"""

import logging
import re
from collections import OrderedDict
from pathlib import Path
from dataclasses import dataclass

from rag.graph_backend import GraphBackend, GraphNode
from rag.code_graph import CodePropertyGraph, build_code_graph

logger = logging.getLogger(__name__)

_TOKEN_CACHE_MAX = 10_000
_SNIPPET_CACHE_MAX = 512
_ANCHOR_CACHE_MAX = 200


@dataclass
class GraphSearchResult:
    """A single result from Graph RAG search."""
    node: GraphNode
    score: float                  # Relevance score (0-1)
    source: str                   # "vector", "graph_call", "graph_import", etc.
    depth: int = 0                # Graph traversal depth from anchor
    code_snippet: str = ""        # Source code snippet

    def to_dict(self) -> dict:
        return {
            "file_path": self.node.file_path,
            "name": self.node.name,
            "node_type": self.node.node_type,
            "signature": self.node.signature,
            "start_line": self.node.start_line,
            "end_line": self.node.end_line,
            "score": round(self.score, 4),
            "source": self.source,
            "depth": self.depth,
        }


class GraphRetriever:
    """
    Graph-enhanced retriever that combines:
    - Semantic similarity (vector search via CodeRetriever)
    - Structural relationships (graph traversal via CodePropertyGraph)

    This produces richer context for the LLM compared to pure vector RAG.
    """

    def __init__(
        self,
        graph: GraphBackend = None,
        repo_path: str = "",
        vector_retriever=None,
    ):
        """
        Args:
            graph: Pre-built graph backend (CodePropertyGraph or Neo4jGraph)
            repo_path: Path to the source repository
            vector_retriever: Optional CodeRetriever for vector search anchor
        """
        self.graph = graph
        self.repo_path = repo_path
        self.vector_retriever = vector_retriever
        self._token_cache: OrderedDict[str, set[str]] = OrderedDict()
        self._snippet_file_cache: OrderedDict[str, list[str]] = OrderedDict()
        self._query_anchor_cache: OrderedDict[str, list[tuple[GraphNode, float]]] = OrderedDict()
        self._name_token_index: dict[str, set[str]] = {}
        if self.graph is not None:
            self._prepare_indexes()

    def build_graph(self, repo_path: str = None, language: str = "auto"):
        """Build the code graph from a repository."""
        path = repo_path or self.repo_path
        if not path:
            raise ValueError("repo_path is required to build graph")

        self.repo_path = path
        self.graph = build_code_graph(path, language=language)
        self._prepare_indexes()
        logger.info(f"Graph built: {self.graph.stats()}")
        return self.graph

    def search(
        self,
        query: str,
        top_k: int = 10,
        graph_depth: int = 2,
        include_callers: bool = True,
        include_callees: bool = True,
        include_siblings: bool = True,
    ) -> list[GraphSearchResult]:
        """
        Perform Graph RAG search.

        1. Find anchor nodes via name matching or vector search.
        2. Expand from anchors via graph traversal.
        3. Rank and return combined results.

        Args:
            query: Natural language query (e.g., "NumberUtils hex parsing")
            top_k: Maximum results to return
            graph_depth: How many hops to traverse from anchor nodes
            include_callers: Include functions that CALL the anchor
            include_callees: Include functions that the anchor CALLS
            include_siblings: Include sibling methods in the same class

        Returns:
            Ranked list of GraphSearchResult
        """
        if self.graph is None:
            logger.warning("No graph available. Building...")
            self.build_graph()

        results: dict[str, GraphSearchResult] = {}

        # Step 1: Find anchor nodes
        anchors = self._find_anchors(query)

        logger.info(f"GraphRAG: Found {len(anchors)} anchor nodes for '{query}'")

        # Step 2: Add anchors as results
        for node, score in anchors:
            snippet = self._load_snippet(node)
            results[node.id] = GraphSearchResult(
                node=node,
                score=score,
                source="anchor",
                depth=0,
                code_snippet=snippet,
            )

        # Step 3: Graph expansion from each anchor
        for node, anchor_score in anchors:
            self._expand_from_anchor(
                results, node, anchor_score,
                graph_depth=graph_depth,
                include_callers=include_callers,
                include_callees=include_callees,
                include_siblings=include_siblings,
            )

        # Step 4: Rank by score (descending)
        ranked = sorted(results.values(), key=lambda r: r.score, reverse=True)
        return ranked[:top_k]

    def search_formatted(self, query: str, top_k: int = 10) -> str:
        """Get formatted search results as a string for LLM context."""
        results = self.search(query, top_k=top_k)

        if not results:
            return f"No Graph RAG results for: '{query}'"

        lines = [f"=== Graph RAG Results for: '{query}' ===\n"]

        for i, r in enumerate(results, 1):
            icon = {
                "anchor": "🎯",
                "callee": "→",
                "caller": "←",
                "sibling": "↔",
                "import": "📦",
                "inherit": "🔗",
                "neighbor": "·",
            }.get(r.source, "·")

            location = f"{r.node.file_path}"
            if r.node.signature:
                location += f" :: {r.node.signature}"

            lines.append(
                f"{i}. {icon} [{r.score:.3f}] {location}"
                f" (L{r.node.start_line}-{r.node.end_line})"
                f" [{r.source}, depth={r.depth}]"
            )

            # Show code snippet (truncated)
            if r.code_snippet:
                snippet_preview = r.code_snippet[:200].replace("\n", " ↵ ")
                lines.append(f"   {snippet_preview}...")

            lines.append("")

        return "\n".join(lines)

    # ─── Private Methods ───

    def _find_anchors(self, query: str) -> list[tuple[GraphNode, float]]:
        """
        Find anchor nodes for a query.
        Uses keyword matching against graph node names.
        """
        cache_key = query.strip().lower()
        if cache_key in self._query_anchor_cache:
            self._query_anchor_cache.move_to_end(cache_key)
            return self._query_anchor_cache[cache_key]

        anchors = []
        query_terms = set(query.lower().split())

        # Remove common stop words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
            "to", "for", "of", "and", "or", "not", "does", "do", "did",
            "handle", "method", "function", "class", "file", "bug", "error",
            "fix", "issue", "can", "should", "this", "that",
        }
        query_terms -= stop_words

        candidate_node_ids: set[str] = set()
        for term in query_terms:
            candidate_node_ids.update(self._name_token_index.get(term, set()))
        if not candidate_node_ids:
            # Fallback when no lexical candidates: scan full graph
            candidate_nodes = self.graph.all_nodes()
        else:
            candidate_nodes = [
                n for n in (self.graph.get_node(nid) for nid in candidate_node_ids)
                if n is not None
            ]

        for node in candidate_nodes:
            if node.node_type in ("module", "symbol", "import"):
                continue

            # Calculate relevance score based on name matching
            score = self._compute_name_score(node, query_terms)

            if score > 0:
                anchors.append((node, score))

        # Sort by score descending, take top anchors
        anchors.sort(key=lambda x: x[1], reverse=True)
        top_anchors = anchors[:5]
        # Keep cache bounded
        if len(self._query_anchor_cache) >= _ANCHOR_CACHE_MAX:
            self._query_anchor_cache.popitem(last=False)
        self._query_anchor_cache[cache_key] = top_anchors
        return top_anchors

    def _compute_name_score(
        self, node: GraphNode, query_terms: set[str]
    ) -> float:
        """Compute relevance score between a node and query terms."""
        score = 0.0

        # Tokenize node name (split camelCase and snake_case)
        name_tokens = self._tokenize(node.name)
        sig_tokens = self._tokenize(node.signature) if node.signature else set()
        doc_tokens = self._tokenize(node.docstring[:200]) if node.docstring else set()

        # Exact name match (highest weight)
        if node.name.lower() in query_terms:
            score += 1.0

        # Token overlap with name (high weight)
        name_overlap = query_terms & name_tokens
        if name_overlap:
            score += 0.5 * len(name_overlap) / max(len(query_terms), 1)

        # Token overlap with signature
        sig_overlap = query_terms & sig_tokens
        if sig_overlap:
            score += 0.3 * len(sig_overlap) / max(len(query_terms), 1)

        # Token overlap with docstring (low weight)
        doc_overlap = query_terms & doc_tokens
        if doc_overlap:
            score += 0.2 * len(doc_overlap) / max(len(query_terms), 1)

        # Boost for specific node types
        if node.node_type == "method" and score > 0:
            score *= 1.1
        elif node.node_type == "class" and score > 0:
            score *= 1.05

        return min(score, 1.0)  # Cap at 1.0

    def _tokenize(self, text: str) -> set[str]:
        """Tokenize text by splitting camelCase, snake_case, and spaces."""
        if not text:
            return set()
        cached = self._token_cache.get(text)
        if cached is not None:
            self._token_cache.move_to_end(text)
            return cached
        tokens = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        tokens = re.split(r"[^a-zA-Z0-9]+", tokens)
        result = {t.lower() for t in tokens if len(t) > 1}
        if len(self._token_cache) >= _TOKEN_CACHE_MAX:
            self._token_cache.popitem(last=False)
        self._token_cache[text] = result
        return result

    def _expand_from_anchor(
        self,
        results: dict[str, GraphSearchResult],
        anchor: GraphNode,
        anchor_score: float,
        graph_depth: int,
        include_callers: bool,
        include_callees: bool,
        include_siblings: bool,
    ):
        """Expand the search from an anchor node via graph traversal."""
        # Callees (functions this anchor calls)
        if include_callees:
            for callee in self.graph.get_callees(anchor.id):
                self._add_graph_result(
                    results, callee, anchor_score * 0.7, "callee", depth=1
                )
                # Second-level callees
                if graph_depth >= 2:
                    for callee2 in self.graph.get_callees(callee.id):
                        self._add_graph_result(
                            results, callee2, anchor_score * 0.4, "callee", depth=2
                        )

        # Callers (functions that call this anchor)
        if include_callers:
            for caller in self.graph.get_callers(anchor.id):
                self._add_graph_result(
                    results, caller, anchor_score * 0.6, "caller", depth=1
                )

        # Siblings (other methods in the same class)
        if include_siblings:
            parent = self.graph.get_parent(anchor.id)
            if parent and parent.node_type == "class":
                for sibling in self.graph.get_children(parent.id):
                    if sibling.id != anchor.id:
                        self._add_graph_result(
                            results, sibling, anchor_score * 0.4, "sibling", depth=1
                        )

        # Imports used by this file
        file_id = f"file::{anchor.file_path}"
        for imported in self.graph.get_imports(file_id):
            self._add_graph_result(
                results, imported, anchor_score * 0.2, "import", depth=1
            )

        # Inheritance (superclasses)
        if anchor.node_type == "class":
            for superclass in self.graph.get_superclasses(anchor.id):
                self._add_graph_result(
                    results, superclass, anchor_score * 0.5, "inherit", depth=1
                )

    def _add_graph_result(
        self,
        results: dict[str, GraphSearchResult],
        node: GraphNode,
        score: float,
        source: str,
        depth: int,
    ):
        """Add a graph-discovered node to results (if not already present with higher score)."""
        if node.id in results:
            if results[node.id].score >= score:
                return  # Already have a better score
        snippet = self._load_snippet(node)
        results[node.id] = GraphSearchResult(
            node=node, score=score, source=source, depth=depth,
            code_snippet=snippet,
        )

    def _load_snippet(self, node: GraphNode, max_lines: int = 30) -> str:
        """Load the source code snippet for a node."""
        if not node.file_path or not self.repo_path:
            return ""
        if node.start_line == 0:
            return ""

        filepath = Path(self.repo_path) / node.file_path
        if not filepath.exists():
            return ""

        try:
            key = str(filepath)
            lines = self._snippet_file_cache.get(key)
            if lines is None:
                lines = filepath.read_text(encoding="utf-8", errors="ignore").split("\n")
                if len(self._snippet_file_cache) >= _SNIPPET_CACHE_MAX:
                    self._snippet_file_cache.popitem(last=False)
                self._snippet_file_cache[key] = lines
            else:
                self._snippet_file_cache.move_to_end(key)
            start = max(0, node.start_line - 1)
            end = min(len(lines), node.start_line - 1 + max_lines)
            if node.end_line:
                end = min(end, node.end_line)
            return "\n".join(lines[start:end])
        except Exception:
            return ""

    def _prepare_indexes(self):
        """Prepare lexical index for faster anchor candidate lookup."""
        self._name_token_index.clear()
        self._file_node_index = None
        if self.graph is None:
            return
        for node in self.graph.all_nodes():
            if node.node_type in ("module", "symbol", "import"):
                continue
            tokens = (
                self._tokenize(node.name)
                | self._tokenize(node.signature or "")
                | self._tokenize((node.docstring or "")[:200])
            )
            for token in tokens:
                self._name_token_index.setdefault(token, set()).add(node.id)

    # ─── E2 public API: anchors + hop distances for priority exploration ───

    def find_anchor_nodes(self, query: str, top_k: int = 5) -> list[tuple[GraphNode, float]]:
        """Public wrapper over anchor matching (E2 priority exploration)."""
        if self.graph is None:
            return []
        return self._find_anchors(query)[:top_k]

    def _get_file_node_index(self) -> dict[str, list[str]]:
        """Lazy file_path → [node_id] index (normalized relative paths)."""
        if getattr(self, "_file_node_index", None) is None:
            index: dict[str, list[str]] = {}
            if self.graph is not None:
                for node in self.graph.all_nodes():
                    fp = (node.file_path or "").replace("\\", "/").lstrip("/")
                    if fp:
                        index.setdefault(fp, []).append(node.id)
            self._file_node_index = index
        return self._file_node_index

    def _nodes_for_file(self, file_path: str, cap: int = 10) -> list[str]:
        """Node ids in a file; tolerates suffix/prefix path mismatches."""
        fp = (file_path or "").replace("\\", "/").lstrip("/")
        if not fp:
            return []
        index = self._get_file_node_index()
        if fp in index:
            return index[fp][:cap]
        for key, ids in index.items():
            if key.endswith("/" + fp) or fp.endswith("/" + key):
                return ids[:cap]
        return []

    def file_hop_distances(
        self, seed_files: list[str], max_hops: int = 4
    ) -> dict[str, int]:
        """
        Multi-source BFS from every node of the seed files, returning the
        minimum hop distance per reachable file. Files not reached within
        max_hops are absent (callers should treat missing as max_hops + 1).
        Used by the E2 explorer to score actions by proximity to anchors.
        """
        if self.graph is None:
            return {}
        distances: dict[str, int] = {}

        def _norm(fp: str) -> str:
            return (fp or "").replace("\\", "/").lstrip("/")

        seed_ids: list[str] = []
        for fp in seed_files or []:
            nfp = _norm(fp)
            if nfp:
                distances[nfp] = 0
                seed_ids.extend(self._nodes_for_file(nfp))

        for nid in seed_ids:
            try:
                for node, depth, _edge in self.graph.get_neighbors(nid, max_depth=max_hops):
                    nfp = _norm(node.file_path)
                    if not nfp:
                        continue
                    if depth < distances.get(nfp, max_hops + 1):
                        distances[nfp] = depth
            except Exception as e:
                logger.debug(f"[GraphRetriever] neighbor BFS failed for {nid}: {e}")
        return distances

    def hop_distance(self, file_a: str, file_b: str, max_hops: int = 4) -> int:
        """
        Minimum hop distance between any node of file_a and any node of
        file_b (BFS over call/containment/import edges). Returns
        ``max_hops + 1`` when unreachable or when the graph is unavailable —
        a neutral value so priority scoring degrades gracefully.
        """
        if self.graph is None:
            return max_hops + 1
        cache = getattr(self, "_hop_distance_cache", None)
        if cache is None:
            cache = self._hop_distance_cache = OrderedDict()
        key = (file_a, file_b, max_hops)
        if key in cache:
            cache.move_to_end(key)
            return cache[key]

        def _norm(fp: str) -> str:
            return (fp or "").replace("\\", "/").lstrip("/")

        result = max_hops + 1
        if _norm(file_a) == _norm(file_b):
            result = 0
        else:
            target_ids = set(self._nodes_for_file(file_b, cap=50))
            target_fp = _norm(file_b)
            for nid in self._nodes_for_file(file_a):
                try:
                    for node, depth, _edge in self.graph.get_neighbors(nid, max_depth=max_hops):
                        if node.id in target_ids or _norm(node.file_path) == target_fp:
                            result = min(result, depth)
                            break
                except Exception:
                    continue
                if result <= 1:
                    break

        if len(cache) >= 2048:
            cache.popitem(last=False)
        cache[key] = result
        return result

"""
Graph RAG API routes.
"""

import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class GraphStats(BaseModel):
    total_nodes: int
    total_edges: int
    node_types: dict
    edge_types: dict


class GraphNode(BaseModel):
    id: str
    type: str
    name: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    docstring: Optional[str] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str


class GraphData(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    stats: GraphStats


class SearchRequest(BaseModel):
    query: str
    repo_path: str
    top_k: int = 10


class SearchResult(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class CallersRequest(BaseModel):
    function_name: str
    repo_path: str


@router.get("/stats", response_model=GraphStats)
async def get_graph_stats(repo_path: str):
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist")

    try:
        from tools.graph_search import get_graph_retriever

        retriever = get_graph_retriever(repo_path)
        stats = retriever.graph.stats()
        return GraphStats(**stats)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data", response_model=GraphData)
async def get_graph_data(
    repo_path: str, focus_file: Optional[str] = None, max_nodes: int = 200
):
    if not os.path.exists(repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist")

    try:
        from rag.graph_backend import InMemoryGraph
        from tools.graph_search import get_graph_retriever

        retriever = get_graph_retriever(repo_path)
        graph = retriever.graph
        stats = graph.stats()

        nodes = []
        edges = []

        node_filter = None
        if focus_file:
            node_filter = (
                lambda n: focus_file.lower()
                in (n.data.get("file_path", "") or "").lower()
            )

        for node in graph.nodes.values():
            if node_filter and not node_filter(node):
                continue
            if len(nodes) >= max_nodes:
                break

            nodes.append(
                GraphNode(
                    id=node.id,
                    type=node.type,
                    name=node.data.get("name", node.id),
                    file_path=node.data.get("file_path"),
                    line_number=node.data.get("line_number"),
                    docstring=node.data.get("docstring", "")[:200]
                    if node.data.get("docstring")
                    else None,
                )
            )

        node_ids = {n.id for n in nodes}
        for edge in graph.edges:
            if edge.source_id in node_ids and edge.target_id in node_ids:
                edges.append(
                    GraphEdge(
                        source=edge.source_id,
                        target=edge.target_id,
                        type=edge.type,
                    )
                )

        return GraphData(nodes=nodes, edges=edges, stats=GraphStats(**stats))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search", response_model=SearchResult)
async def search_graph(request: SearchRequest):
    if not os.path.exists(request.repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist")

    try:
        from rag.graph_retriever import GraphRetriever

        retriever = GraphRetriever(repo_path=request.repo_path)
        retriever.build_graph(language="auto")

        results = retriever.search(request.query, top_k=request.top_k)

        nodes = []
        edges = []
        for node in results:
            nodes.append(
                GraphNode(
                    id=node.id,
                    type=node.type,
                    name=node.data.get("name", node.id),
                    file_path=node.data.get("file_path"),
                    line_number=node.data.get("line_number"),
                )
            )

        return SearchResult(nodes=nodes, edges=edges)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/callers")
async def find_callers(request: CallersRequest):
    if not os.path.exists(request.repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist")

    try:
        from tools.graph_search import find_callers as _find_callers

        result = _find_callers(request.function_name, request.repo_path)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

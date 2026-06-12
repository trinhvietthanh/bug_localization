"""
Code Property Graph Visualization.
Generates interactive HTML and static PNG/SVG visualizations of the code graph.

Outputs:
- Interactive HTML (pyvis): Zoom, drag, hover to explore relationships
- Static PNG/SVG (matplotlib): For thesis report figures
"""

import logging
from pathlib import Path

import networkx as nx

from rag.code_graph import CodePropertyGraph

logger = logging.getLogger(__name__)


# ──────────────────────── Color Palette ────────────────────────

NODE_COLORS = {
    "file":     "#4A90D9",   # Blue
    "class":    "#E74C3C",   # Red
    "method":   "#2ECC71",   # Green
    "function": "#F39C12",   # Orange
    "module":   "#9B59B6",   # Purple
    "symbol":   "#9B59B6",   # Purple
    "import":   "#9B59B6",   # Purple
}

NODE_SIZES = {
    "file":     30,
    "class":    25,
    "method":   15,
    "function": 18,
    "module":   12,
    "symbol":   10,
    "import":   10,
}

EDGE_COLORS = {
    "CALLS":     "#E74C3C",   # Red
    "CONTAINS":  "#BDC3C7",   # Light gray
    "IMPORTS":   "#9B59B6",   # Purple
    "INHERITS":  "#3498DB",   # Blue
}

NODE_SHAPES = {
    "file":     "square",
    "class":    "diamond",
    "method":   "dot",
    "function": "triangle",
    "module":   "star",
    "symbol":   "star",
    "import":   "star",
}


def visualize_interactive(
    cpg: CodePropertyGraph,
    output_path: str = "results/code_graph.html",
    title: str = "Code Property Graph",
    max_nodes: int = 200,
    filter_types: list[str] = None,
    focus_file: str = None,
    height: str = "900px",
    width: str = "100%",
):
    """
    Generate an interactive HTML visualization using pyvis.

    Args:
        cpg: The Code Property Graph
        output_path: Path to save the HTML file
        title: Title of the visualization
        max_nodes: Maximum nodes to display (for performance)
        filter_types: Only show these node types (e.g., ["class", "method"])
        focus_file: Only show nodes from this file (and their connections)
        height: Height of the visualization
        width: Width of the visualization
    """
    from pyvis.network import Network

    net = Network(
        height=height, width=width,
        directed=True,
        notebook=False,
        bgcolor="#1a1a2e",
        font_color="#ffffff",
    )

    # Physics configuration for better layout
    net.set_options("""
    {
        "physics": {
            "forceAtlas2Based": {
                "gravitationalConstant": -80,
                "centralGravity": 0.01,
                "springLength": 120,
                "springConstant": 0.08,
                "damping": 0.4
            },
            "solver": "forceAtlas2Based",
            "stabilization": {
                "enabled": true,
                "iterations": 200
            }
        },
        "edges": {
            "smooth": {
                "type": "curvedCW",
                "roundness": 0.15
            },
            "arrows": {
                "to": {"enabled": true, "scaleFactor": 0.5}
            }
        },
        "interaction": {
            "hover": true,
            "tooltipDelay": 100,
            "navigationButtons": true,
            "keyboard": true
        }
    }
    """)

    # Filter nodes
    nodes_to_show = set()

    if focus_file:
        # Show only nodes related to a specific file
        for node in cpg.nodes.values():
            if focus_file in node.file_path:
                nodes_to_show.add(node.id)
        # Add neighbors
        expanded = set()
        for nid in nodes_to_show:
            neighbors = cpg.get_neighbors(nid, max_depth=1)
            for neighbor, _, _ in neighbors:
                expanded.add(neighbor.id)
        nodes_to_show |= expanded
    elif filter_types:
        for node in cpg.nodes.values():
            if node.node_type in filter_types:
                nodes_to_show.add(node.id)
    else:
        # Show all (up to max_nodes)
        # Prioritize classes and methods over imports/modules
        priority_order = ["class", "method", "function", "file", "module", "symbol", "import"]
        for ntype in priority_order:
            for node in cpg.nodes.values():
                if node.node_type == ntype:
                    nodes_to_show.add(node.id)
                    if len(nodes_to_show) >= max_nodes:
                        break
            if len(nodes_to_show) >= max_nodes:
                break

    # Add nodes
    for nid in nodes_to_show:
        node = cpg.nodes.get(nid)
        if not node:
            continue

        color = NODE_COLORS.get(node.node_type, "#95A5A6")
        size = NODE_SIZES.get(node.node_type, 15)
        shape = NODE_SHAPES.get(node.node_type, "dot")

        # Build tooltip
        tooltip = (
            f"<b>{node.name}</b><br>"
            f"Type: {node.node_type}<br>"
            f"File: {node.file_path}<br>"
        )
        if node.signature:
            tooltip += f"Signature: {node.signature}<br>"
        if node.start_line:
            tooltip += f"Lines: {node.start_line}-{node.end_line}<br>"

        net.add_node(
            nid,
            label=node.name,
            color=color,
            size=size,
            shape=shape,
            title=tooltip,
            font={"size": 10},
        )

    # Add edges
    for edge in cpg.edges:
        if edge.source_id in nodes_to_show and edge.target_id in nodes_to_show:
            color = EDGE_COLORS.get(edge.edge_type, "#BDC3C7")
            width = 2 if edge.edge_type == "CALLS" else 1

            net.add_edge(
                edge.source_id,
                edge.target_id,
                color=color,
                width=width,
                title=edge.edge_type,
            )

    # Add legend as title
    legend_html = f"""
    <div style="position:absolute; top:10px; left:10px; background:rgba(0,0,0,0.7);
                padding:15px; border-radius:10px; font-family:monospace; z-index:1000;">
        <h3 style="margin:0 0 10px 0; color:#fff;">{title}</h3>
        <div style="color:#4A90D9;">■ File</div>
        <div style="color:#E74C3C;">◆ Class</div>
        <div style="color:#2ECC71;">● Method</div>
        <div style="color:#F39C12;">▲ Function</div>
        <div style="color:#9B59B6;">★ Import/Module</div>
        <hr style="border-color:#555;">
        <div style="color:#E74C3C;">── CALLS</div>
        <div style="color:#BDC3C7;">── CONTAINS</div>
        <div style="color:#9B59B6;">── IMPORTS</div>
        <div style="color:#3498DB;">── INHERITS</div>
        <hr style="border-color:#555;">
        <div style="color:#888;">Nodes: {len(nodes_to_show)} | Edges: {len(cpg.edges)}</div>
    </div>
    """

    # Save
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(out))

    # Inject legend into HTML
    html_content = out.read_text()
    html_content = html_content.replace("<body>", f"<body>{legend_html}")
    out.write_text(html_content)

    logger.info(f"Interactive graph saved: {output_path} ({len(nodes_to_show)} nodes)")
    return str(out)


def visualize_static(
    cpg: CodePropertyGraph,
    output_path: str = "results/code_graph.png",
    title: str = "Code Property Graph",
    max_nodes: int = 100,
    focus_file: str = None,
    figsize: tuple = (20, 14),
    dpi: int = 150,
):
    """
    Generate a static PNG/SVG visualization using matplotlib.

    Args:
        cpg: The Code Property Graph
        output_path: Path to save the image (supports .png, .svg, .pdf)
        title: Title of the figure
        max_nodes: Maximum nodes to display
        focus_file: Only show nodes from this file
        figsize: Figure size (width, height) in inches
        dpi: Resolution
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    G = nx.DiGraph()

    # Filter nodes
    nodes_to_show = set()

    if focus_file:
        for node in cpg.nodes.values():
            if focus_file in node.file_path:
                nodes_to_show.add(node.id)
        expanded = set()
        for nid in nodes_to_show:
            neighbors = cpg.get_neighbors(nid, max_depth=1)
            for neighbor, _, _ in neighbors:
                expanded.add(neighbor.id)
        nodes_to_show |= expanded
    else:
        # Prioritize important nodes
        for node in cpg.nodes.values():
            if node.node_type in ("class", "method", "function"):
                nodes_to_show.add(node.id)
                if len(nodes_to_show) >= max_nodes:
                    break

    # Build subgraph
    for nid in nodes_to_show:
        node = cpg.nodes.get(nid)
        if node:
            G.add_node(nid, **{
                "label": node.name,
                "node_type": node.node_type,
            })

    for edge in cpg.edges:
        if edge.source_id in nodes_to_show and edge.target_id in nodes_to_show:
            G.add_edge(edge.source_id, edge.target_id, edge_type=edge.edge_type)

    if len(G.nodes) == 0:
        logger.warning("No nodes to visualize")
        return

    # Layout
    try:
        pos = nx.spring_layout(G, k=2.0, iterations=50, seed=42)
    except Exception:
        pos = nx.circular_layout(G)

    _, ax = plt.subplots(figsize=figsize, facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    # Draw edges by type
    for edge_type, color in EDGE_COLORS.items():
        edge_list = [
            (u, v) for u, v, d in G.edges(data=True)
            if d.get("edge_type") == edge_type
        ]
        if edge_list:
            nx.draw_networkx_edges(
                G, pos,
                edgelist=edge_list,
                edge_color=color,
                width=1.5 if edge_type == "CALLS" else 0.8,
                alpha=0.6 if edge_type == "CONTAINS" else 0.8,
                arrows=True,
                arrowsize=10,
                connectionstyle="arc3,rad=0.1",
                ax=ax,
            )

    # Draw nodes by type
    for node_type, color in NODE_COLORS.items():
        node_list = [
            n for n, d in G.nodes(data=True)
            if d.get("node_type") == node_type
        ]
        if node_list:
            size = NODE_SIZES.get(node_type, 15) * 20
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=node_list,
                node_color=color,
                node_size=size,
                alpha=0.9,
                ax=ax,
            )

    # Labels
    labels = {n: d.get("label", n.split("::")[-1]) for n, d in G.nodes(data=True)}
    nx.draw_networkx_labels(
        G, pos, labels,
        font_size=6,
        font_color="#ffffff",
        font_weight="bold",
        ax=ax,
    )

    # Legend
    legend_handles = []
    for ntype, color in NODE_COLORS.items():
        if any(d.get("node_type") == ntype for _, d in G.nodes(data=True)):
            legend_handles.append(
                mpatches.Patch(color=color, label=ntype.capitalize())
            )
    for etype, color in EDGE_COLORS.items():
        if any(d.get("edge_type") == etype for _, _, d in G.edges(data=True)):
            legend_handles.append(
                mpatches.Patch(color=color, label=f"── {etype}")
            )

    ax.legend(
        handles=legend_handles,
        loc="upper left",
        facecolor="#2d2d44",
        edgecolor="#555",
        labelcolor="#fff",
        fontsize=8,
    )

    ax.set_title(title, color="#ffffff", fontsize=16, fontweight="bold", pad=20)
    ax.axis("off")

    plt.tight_layout()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out), dpi=dpi, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close()

    logger.info(f"Static graph saved: {output_path} ({len(G.nodes)} nodes)")
    return str(out)

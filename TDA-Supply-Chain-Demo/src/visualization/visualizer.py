"""
visualization/visualizer.py
============================
Visualization tools:
- Topological heatmap of the supply-chain graph
- Persistence barcode and diagram plots
- Persistence landscape plots
- Anomaly timeline dashboard
- Export to PNG / interactive HTML
"""

from __future__ import annotations

import json
import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.core.tda_engine import (
    PersistenceDiagram,
    DynamicFilteredComplex,
    persistence_landscape,
)
from src.core.anomaly_detector import AnomalyAlert

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Color utilities
# ---------------------------------------------------------------------------

DIM_COLORS = {0: "#2196F3", 1: "#FF5722", 2: "#4CAF50"}
SEVERITY_COLORS = {
    "low": "#8BC34A",
    "medium": "#FFC107",
    "high": "#FF5722",
    "critical": "#D32F2F",
}


def weight_to_hex(weight: float) -> str:
    """Map filtration weight 0→1 to a green→red hex color."""
    r = int(min(255, weight * 2 * 255))
    g = int(min(255, (1 - weight) * 2 * 255))
    return f"#{r:02x}{g:02x}00"


# ---------------------------------------------------------------------------
# ASCII / text heatmap (for console / logging)
# ---------------------------------------------------------------------------

def ascii_heatmap(complex: DynamicFilteredComplex,
                  alert: Optional[AnomalyAlert] = None,
                  width: int = 60) -> str:
    """Render a simple ASCII weight histogram for the edge weight distribution."""
    weights = [
        s.filtration_value
        for key, s in complex.simplices.items()
        if len(key) == 2
    ]
    if not weights:
        return "(no edges in complex)"

    n_bins = 20
    counts, bin_edges = np.histogram(weights, bins=n_bins, range=(0, 1))
    max_count = max(counts) if counts.max() > 0 else 1

    lines = ["Edge weight distribution (0=normal, 1=anomalous):"]
    for i, (c, lo) in enumerate(zip(counts, bin_edges)):
        bar_len = int(c / max_count * width)
        bar = "█" * bar_len
        lines.append(f"{lo:.2f} |{bar:<{width}}| {c}")

    if alert and alert.is_anomaly:
        lines.append(f"\n⚠ ANOMALY: score={alert.anomaly_score:.4f} | {alert.description}")
        lines.append(f"  Affected nodes: {alert.contributing_nodes[:5]}")
        lines.append(f"  Affected edges: {alert.contributing_edges[:5]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Matplotlib-based plots (optional dependency)
# ---------------------------------------------------------------------------

def plot_persistence_diagram(diagram: PersistenceDiagram,
                              save_path: Optional[str] = None,
                              title: str = "Persistence Diagram"):
    """
    Plot birth-death diagram for H0, H1, H2.
    Infinite bars shown as triangles at the top.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        logger.warning("matplotlib not installed; skipping persistence diagram plot")
        return None

    fig, ax = plt.subplots(1, 1, figsize=(7, 7))

    all_finite = [(p.birth, p.death)
                  for p in diagram.pairs if not np.isinf(p.death)]
    if all_finite:
        max_val = max(d for _, d in all_finite)
    else:
        max_val = 1.0

    # Diagonal
    ax.plot([0, max_val], [0, max_val], 'k--', linewidth=0.8, alpha=0.5)

    for dim in range(3):
        pts = diagram.pairs_by_dim(dim)
        color = DIM_COLORS[dim]
        for p in pts:
            b = p.birth
            d = p.death if not np.isinf(p.death) else max_val * 1.05
            ax.scatter([b], [d], c=color, s=30, alpha=0.7, zorder=3)

    patches = [mpatches.Patch(color=DIM_COLORS[d], label=f"H{d}") for d in range(3)]
    ax.legend(handles=patches)
    ax.set_xlabel("Birth")
    ax.set_ylabel("Death")
    ax.set_title(title)
    ax.set_xlim(-0.02, max_val * 1.1)
    ax.set_ylim(-0.02, max_val * 1.1)

    if save_path:
        fig.savefig(save_path, dpi=120, bbox_inches="tight")
        logger.info(f"Saved persistence diagram to {save_path}")
    return fig


def plot_barcode(diagram: PersistenceDiagram,
                 save_path: Optional[str] = None):
    """Plot persistence barcode (horizontal bars = feature lifetimes)."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        return None

    finite_pairs = [(p.dimension, p.birth, p.death)
                    for p in diagram.pairs if not np.isinf(p.death)
                    and p.persistence > 0.005]
    finite_pairs.sort(key=lambda x: (x[0], x[2] - x[1]), reverse=True)

    if not finite_pairs:
        return None

    fig, ax = plt.subplots(figsize=(10, max(4, len(finite_pairs) * 0.25)))
    for i, (dim, b, d) in enumerate(finite_pairs):
        ax.barh(y=i, width=d - b, left=b, color=DIM_COLORS[dim],
                height=0.6, alpha=0.75)

    patches = [mpatches.Patch(color=DIM_COLORS[d], label=f"H{d}") for d in range(3)]
    ax.legend(handles=patches, loc="upper right")
    ax.set_xlabel("Filtration value")
    ax.set_ylabel("Feature index")
    ax.set_title("Persistence Barcode")

    if save_path:
        fig.savefig(save_path, dpi=120, bbox_inches="tight")
    return fig


def plot_landscape(diagram: PersistenceDiagram,
                   dim: int = 1,
                   n_layers: int = 5,
                   save_path: Optional[str] = None):
    """Plot persistence landscape for dimension dim."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    landscape = persistence_landscape(diagram, dim=dim, n_layers=n_layers)
    resolution = landscape.shape[1]
    x = np.linspace(0, 1, resolution)

    fig, ax = plt.subplots(figsize=(10, 4))
    for k in range(n_layers):
        ax.plot(x, landscape[k], label=f"λ_{k+1}", alpha=0.8 - k * 0.1)

    ax.set_xlabel("Filtration value")
    ax.set_ylabel("Landscape value")
    ax.set_title(f"Persistence Landscape — H{dim}")
    ax.legend()

    if save_path:
        fig.savefig(save_path, dpi=120, bbox_inches="tight")
    return fig


def plot_anomaly_timeline(alerts: List[AnomalyAlert],
                           save_path: Optional[str] = None):
    """Plot anomaly score over time with alert markers."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime
    except ImportError:
        return None

    if not alerts:
        return None

    times = [datetime.fromtimestamp(a.timestamp) for a in alerts]
    scores = [a.anomaly_score for a in alerts]
    thresholds = [a.threshold for a in alerts]
    anomaly_flags = [a.is_anomaly for a in alerts]

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(times, scores, "b-", linewidth=1.5, label="Anomaly score", zorder=2)
    ax.plot(times, thresholds, "r--", linewidth=1.0, label="Threshold", alpha=0.7)

    for t, s, flag, alert in zip(times, scores, anomaly_flags, alerts):
        if flag:
            color = SEVERITY_COLORS.get(alert.severity, "red")
            ax.scatter([t], [s], c=color, s=100, zorder=4, marker="v")

    ax.fill_between(times, scores, thresholds,
                    where=[s > th for s, th in zip(scores, thresholds)],
                    alpha=0.2, color="red", label="Alert zone")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlabel("Time")
    ax.set_ylabel("Wasserstein distance")
    ax.set_title("Topological Anomaly Score Timeline")
    ax.legend()
    ax.grid(True, alpha=0.3)

    if save_path:
        fig.savefig(save_path, dpi=120, bbox_inches="tight")
    return fig


# ---------------------------------------------------------------------------
# Interactive HTML heatmap export (no external dependencies)
# ---------------------------------------------------------------------------

def export_html_heatmap(complex: DynamicFilteredComplex,
                         alert: Optional[AnomalyAlert],
                         diagram: Optional[PersistenceDiagram],
                         output_path: str):
    """
    Generate a self-contained HTML file with:
    - D3.js force-directed network graph
    - Color-coded nodes/edges by anomaly weight
    - Highlighted anomalous subgraph
    - Betti number panel
    """
    nodes_data = []
    for key, simplex in complex.simplices.items():
        if len(key) == 1:
            nid = key[0]
            is_anomalous = alert and nid in alert.contributing_nodes
            nodes_data.append({
                "id": nid,
                "weight": round(simplex.filtration_value, 4),
                "anomalous": bool(is_anomalous),
            })

    edges_data = []
    anomalous_edge_set = set()
    if alert:
        anomalous_edge_set = {tuple(sorted(e)) for e in alert.contributing_edges}

    for key, simplex in complex.simplices.items():
        if len(key) == 2:
            u, v = key
            is_anomalous = tuple(sorted([u, v])) in anomalous_edge_set
            edges_data.append({
                "source": u,
                "target": v,
                "weight": round(simplex.filtration_value, 4),
                "anomalous": bool(is_anomalous),
            })

    betti = diagram.betti_numbers if diagram else {}
    alert_data = alert.to_dict() if alert else {}

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TDA Supply Chain Heatmap</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #e6edf3; margin: 0; }}
  #header {{ padding: 16px 24px; background: #161b22; border-bottom: 1px solid #30363d; }}
  h1 {{ margin: 0; font-size: 20px; color: #58a6ff; }}
  #main {{ display: flex; height: calc(100vh - 60px); }}
  #graph {{ flex: 1; }}
  #sidebar {{ width: 300px; background: #161b22; border-left: 1px solid #30363d; padding: 16px; overflow-y: auto; }}
  .metric {{ margin-bottom: 12px; }}
  .metric-label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; }}
  .metric-value {{ font-size: 22px; font-weight: bold; }}
  .alert-box {{ background: #1c1f26; border: 1px solid #f85149; border-radius: 6px; padding: 12px; margin: 12px 0; }}
  .alert-box h3 {{ margin: 0 0 8px; color: #f85149; font-size: 14px; }}
  .normal-box {{ border-color: #3fb950; }}
  .normal-box h3 {{ color: #3fb950; }}
  .edge {{ stroke-opacity: 0.6; }}
  .node circle {{ stroke-width: 2px; }}
  .tooltip {{ position: absolute; background: rgba(13,17,23,0.95); border: 1px solid #30363d;
              border-radius: 6px; padding: 8px 12px; font-size: 12px; pointer-events: none; }}
  .legend {{ display: flex; gap: 12px; margin-top: 8px; }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 11px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
</style>
</head>
<body>
<div id="header">
  <h1>🌐 TDA Supply Chain Anomaly Heatmap</h1>
</div>
<div id="main">
  <svg id="graph"></svg>
  <div id="sidebar">
    <div class="metric">
      <div class="metric-label">Nodes</div>
      <div class="metric-value" id="n-nodes">{len(nodes_data)}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Edges</div>
      <div class="metric-value" id="n-edges">{len(edges_data)}</div>
    </div>
    <div class="metric">
      <div class="metric-label">H₀ (Connected Components)</div>
      <div class="metric-value" style="color:#2196F3">{betti.get(0,'—')}</div>
    </div>
    <div class="metric">
      <div class="metric-label">H₁ (Routing Loops)</div>
      <div class="metric-value" style="color:#FF5722">{betti.get(1,'—')}</div>
    </div>
    <div class="metric">
      <div class="metric-label">H₂ (Voids)</div>
      <div class="metric-value" style="color:#4CAF50">{betti.get(2,'—')}</div>
    </div>
    <div id="alert-panel"></div>
    <div class="legend">
      <div class="legend-item"><div class="legend-dot" style="background:#3fb950"></div> Normal</div>
      <div class="legend-item"><div class="legend-dot" style="background:#d29922"></div> Warning</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f85149"></div> Anomalous</div>
    </div>
  </div>
</div>
<div class="tooltip" id="tooltip" style="display:none"></div>
<script>
const nodesData = {json.dumps(nodes_data)};
const edgesData = {json.dumps(edges_data)};
const alertData = {json.dumps(alert_data)};

// Alert panel
const panel = document.getElementById('alert-panel');
if (alertData.is_anomaly) {{
  panel.innerHTML = `<div class="alert-box"><h3>⚠ ANOMALY DETECTED</h3>
    <p style="font-size:12px">${{alertData.description}}</p>
    <p style="font-size:12px">Score: ${{alertData.anomaly_score?.toFixed(4)}}</p>
    <p style="font-size:12px">Severity: <b style="text-transform:capitalize">${{alertData.severity}}</b></p>
    </div>`;
}} else {{
  panel.innerHTML = `<div class="alert-box normal-box"><h3>✓ Normal Operation</h3>
    <p style="font-size:12px">Score: ${{(alertData.anomaly_score||0).toFixed(4)}}</p></div>`;
}}

// D3 force graph
const svg = d3.select('#graph');
const width = document.getElementById('graph').clientWidth || 900;
const height = document.getElementById('graph').clientHeight || 600;
svg.attr('viewBox', [0, 0, width, height]);

const sim = d3.forceSimulation(nodesData)
  .force('link', d3.forceLink(edgesData).id(d => d.id).distance(60))
  .force('charge', d3.forceManyBody().strength(-80))
  .force('center', d3.forceCenter(width/2, height/2));

function weightColor(w, anomalous) {{
  if (anomalous) return '#f85149';
  if (w > 0.6) return '#d29922';
  if (w > 0.3) return '#e3b341';
  return '#3fb950';
}}

const link = svg.append('g').selectAll('line')
  .data(edgesData).join('line')
  .attr('class', 'edge')
  .attr('stroke', d => weightColor(d.weight, d.anomalous))
  .attr('stroke-width', d => d.anomalous ? 3 : 1.5);

const node = svg.append('g').selectAll('g')
  .data(nodesData).join('g')
  .call(d3.drag()
    .on('start', (e,d) => {{ if(!e.active) sim.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }})
    .on('drag', (e,d) => {{ d.fx=e.x; d.fy=e.y; }})
    .on('end', (e,d) => {{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}));

node.append('circle')
  .attr('r', d => d.anomalous ? 10 : 6)
  .attr('fill', d => weightColor(d.weight, d.anomalous))
  .attr('stroke', d => d.anomalous ? '#fff' : 'transparent');

const tooltip = document.getElementById('tooltip');
node.on('mouseover', (e, d) => {{
  tooltip.style.display = 'block';
  tooltip.innerHTML = `Node ${{d.id}}<br>Weight: ${{d.weight.toFixed(4)}}<br>${{d.anomalous ? '⚠ Anomalous' : 'Normal'}}`;
}}).on('mousemove', e => {{
  tooltip.style.left = (e.pageX + 12) + 'px';
  tooltip.style.top = (e.pageY - 10) + 'px';
}}).on('mouseout', () => {{ tooltip.style.display = 'none'; }});

sim.on('tick', () => {{
  link.attr('x1', d=>d.source.x).attr('y1', d=>d.source.y)
      .attr('x2', d=>d.target.x).attr('y2', d=>d.target.y);
  node.attr('transform', d=>`translate(${{d.x}},${{d.y}})`);
}});
</script>
</body></html>"""

    with open(output_path, "w") as f:
        f.write(html)
    logger.info(f"HTML heatmap saved to {output_path}")
    return output_path

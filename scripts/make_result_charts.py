#!/usr/bin/env python3
"""Generate clean result charts for the thesis presentation.

Outputs (docs/figures/):
  - chart_system_compare.png   Top-1 vs SOTA (horizontal bar)
  - chart_bare_vs_agent.png    Top-1/3/5 gap bare-vs-agent (grouped bar)
  - chart_ablation.png         Top-1 across ablation configs
  - chart_cost_breakdown.png   LLM calls/instance donut
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

# --- Style -----------------------------------------------------------------
FONT = FontProperties(fname="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_B = FontProperties(fname="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

NAVY = "#1565C0"      # đề xuất / hệ thống
NAVY_D = "#0D47A1"
ORANGE = "#EF6C00"    # baseline / bare
GREEN = "#2E7D32"
GRAY = "#9E9E9E"
RED = "#C62828"
LIGHT = "#E3F2FD"

plt.rcParams.update({
    "axes.edgecolor": "#424242",
    "axes.linewidth": 0.8,
    "figure.dpi": 200,
    "savefig.dpi": 200,
})

OUT = Path(__file__).resolve().parent.parent / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)


def _label_h(ax, bars, fmt="{:.0f}", dx=0.8, color="#212121", size=11):
    """Label horizontal bars to the right of the bar end."""
    for b in bars:
        w = b.get_width()
        ax.text(w + dx, b.get_y() + b.get_height() / 2, fmt.format(w),
                ha="left", va="center",
                fontproperties=FONT_B, fontsize=size, color=color)


# 1) System comparison vs SOTA ------------------------------------------------
def chart_system_compare():
    systems = [
        ("Agentless\n(GPT-4o)", 63.0, GRAY),
        ("CoSIL\n(Qwen2.5-32B)", 61.3, GRAY),
        ("Bare-LLM\n(qwen-plus, 1 call)", 64.0, ORANGE),
        ("LocAgent\n(Claude-3.5)", 77.7, "#7986CB"),
        ("Hệ thống đề xuất\n(qwen-plus, E1+E2+E3)", 78.0, NAVY),
        ("BLAgent\n(GPT-OSS-120B)", 78.6, "#7986CB"),
    ]
    systems.sort(key=lambda s: s[1])
    labels = [s[0] for s in systems]
    vals = [s[1] for s in systems]
    colors = [s[2] for s in systems]

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    bars = ax.barh(labels, vals, color=colors, edgecolor="white", height=0.62)
    _label_h(ax, bars, fmt="{:.1f}", dx=0.8)
    ax.set_xlim(0, 90)
    ax.set_xlabel("Top-1 Accuracy (%) trên SWE-bench Lite", fontproperties=FONT, fontsize=12)
    ax.set_title("So sánh Top-1 với các hệ thống SOTA (file-level localization)",
                 fontproperties=FONT_B, fontsize=14, pad=12, color="#212121")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    for lbl in ax.get_yticklabels():
        lbl.set_fontproperties(FONT)
        lbl.set_fontsize(10.5)
    ax.xaxis.grid(True, color="#E0E0E0", lw=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / "chart_system_compare.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


# 2) Bare vs Agent gap (Top-1/3/5) --------------------------------------------
def chart_bare_vs_agent():
    import numpy as np
    metrics = ["Top-1", "Top-3", "Top-5"]
    bare = [64.0, 83.7, 87.0]
    agent = [78.0, 87.3, 89.3]
    x = np.arange(len(metrics))
    w = 0.36

    fig, ax = plt.subplots(figsize=(10.0, 5.6))
    b1 = ax.bar(x - w / 2, bare, w, label="Bare-LLM (1 call)",
                color=ORANGE, edgecolor="white")
    b2 = ax.bar(x + w / 2, agent, w, label="Hệ thống đề xuất (E1+E2+E3)",
                color=NAVY, edgecolor="white")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.6,
                    f"{b.get_height():.1f}", ha="center", va="bottom",
                    fontproperties=FONT_B, fontsize=10.5, color="#212121")
    # delta annotations
    for i in range(3):
        d = agent[i] - bare[i]
        ax.annotate(f"+{d:.1f}", xy=(x[i], max(agent[i], bare[i]) + 4.5),
                    ha="center", fontproperties=FONT_B, fontsize=10.5,
                    color=GREEN if d >= 3 else GRAY)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics, fontproperties=FONT, fontsize=12)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Accuracy (%)", fontproperties=FONT, fontsize=12)
    ax.set_title("Giá trị gia tăng của khung agent so với bare-LLM (n=300, paired)",
                 fontproperties=FONT_B, fontsize=14, pad=12, color="#212121")
    leg = ax.legend(prop=FONT, fontsize=11, loc="lower right", frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.yaxis.grid(True, color="#E0E0E0", lw=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / "chart_bare_vs_agent.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


# 3) Ablation: agent loop cannot be compressed --------------------------------
def chart_ablation():
    import numpy as np
    configs = [
        ("Baseline\n(E1+E2+E3)", 80.0, NAVY),
        ("Comp.\nsingle-shot", 74.0, ORANGE),
        ("Comp. self-\nconsistency", 72.0, ORANGE),
        ("Conf.\nsingle-shot", 70.0, RED),
        ("Conf.\nhybrid", 74.0, ORANGE),
    ]
    labels = [c[0] for c in configs]
    vals = [c[1] for c in configs]
    colors = [c[2] for c in configs]
    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    bars = ax.bar(x, vals, color=colors, edgecolor="white", width=0.6)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.8,
                f"{b.get_height():.0f}", ha="center", va="bottom",
                fontproperties=FONT_B, fontsize=11, color="#212121")
    ax.axhline(80.0, color=NAVY_D, lw=1.4, ls="--", alpha=0.6)
    ax.text(len(labels) - 0.5, 80.6, "Baseline 80%", fontproperties=FONT_B,
            fontsize=10, color=NAVY_D, ha="right")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontproperties=FONT, fontsize=10.5)
    ax.set_ylim(0, 92)
    ax.set_ylabel("Top-1 Accuracy (%) — subset n=50", fontproperties=FONT, fontsize=12)
    ax.set_title("Mọi cố gắng nén vòng lặp agent đều giảm Top-1 (−6 đến −10 điểm)",
                 fontproperties=FONT_B, fontsize=13.5, pad=12, color="#212121")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.yaxis.grid(True, color="#E0E0E0", lw=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / "chart_ablation.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


# 4) LLM cost breakdown (donut) -----------------------------------------------
def chart_cost_breakdown():
    parts = [
        ("Navigation / Explorer", 12.0, NAVY),
        ("Confirmation", 10.4, "#42A5F5"),
        ("Reflection re-run", 9.0, ORANGE),
        ("Comprehension", 2.0, GREEN),
        ("Rerank + Patch Duel", 1.3, "#AB47BC"),
    ]
    labels = [p[0] for p in parts]
    vals = [p[1] for p in parts]
    colors = [p[2] for p in parts]
    total = sum(vals)

    fig, ax = plt.subplots(figsize=(8.4, 5.6))
    wedges, _ = ax.pie(vals, colors=colors, startangle=90,
                       wedgeprops=dict(width=0.38, edgecolor="white", linewidth=2))
    ax.text(0, 0.08, f"{total:.0f}", ha="center", va="center",
            fontproperties=FONT_B, fontsize=30, color="#212121")
    ax.text(0, -0.18, "LLM calls\n/ instance", ha="center", va="center",
            fontproperties=FONT, fontsize=11, color="#616161")

    leg_labels = [f"{l} — {v:.1f} ({v/total*100:.0f}%)" for l, v in zip(labels, vals)]
    ax.legend(wedges, leg_labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
              prop=FONT, fontsize=10.5, frameon=False)
    ax.set_title("Phân bố chi phí LLM (≈ 34.7 calls/instance)",
                 fontproperties=FONT_B, fontsize=13.5, pad=10, color="#212121")
    fig.tight_layout()
    fig.savefig(OUT / "chart_cost_breakdown.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    chart_system_compare()
    chart_bare_vs_agent()
    chart_ablation()
    chart_cost_breakdown()
    print("Charts written to", OUT)
    for p in sorted(OUT.glob("chart_*.png")):
        print(" -", p.name, f"{p.stat().st_size//1024} KB")

#!/usr/bin/env python3
"""
Cross-model comparison harness for the contamination / memorization study.

For each model, runs BOTH configurations on the SAME set of SWE-bench instances:

  full  — the complete agentic system (scripts/run_swebench_benchmark.py)
  bare  — a no-agent one-shot LLM baseline  (scripts/bare_llm_baseline.py)

and produces a model x mode grid plus, per model, delta(full - bare): the
agentic framework's marginal contribution on top of what the raw model already
knows. Because contamination inflates BOTH rows of a given model roughly
equally, the per-model delta is the number that survives "the model just
memorized the benchmark" — and a delta that holds across models with different
memorization profiles is the real evidence the method works.

Instances are pinned by the checkout directory (data/swebench_checkouts/), so
every (model, mode) run evaluates the identical instance set. Apply --limit to
all runs uniformly for a cheaper first pass.

Keys are read at launch from the environment:
  DASHSCOPE_KEY      qwen-plus reference  (falls back to OPENAI_API_KEY)
  OPENROUTER_API_KEY all OpenRouter models (gpt-4o-mini, deepseek, qwen-coder-7b, ...)
                     — one key, many vendors; ideal for the cross-vendor contrast

Usage:
    # quick pass on 30 instances, all models, both modes
    python scripts/run_cross_model.py --limit 30 --workers 4

    # only the cheap qwen tiers first
    python scripts/run_cross_model.py --models qwen-plus,qwen-turbo --modes full,bare

    # resume: skip (model,mode) cells that already produced output
    python scripts/run_cross_model.py --resume

    # just (re)build the report from existing results
    python scripts/run_cross_model.py --report-only
"""

import os
import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env into os.environ so DASHSCOPE_KEY / OPENAI_API_KEY / OPENAI_GPT_KEY
# resolve when this harness is launched from a shell. (config.py also calls this
# in each subprocess, with override=False, so injected values survive there.)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

OUT_DIR = PROJECT_ROOT / "results" / "cross_model"
PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
BENCH = str(PROJECT_ROOT / "scripts" / "run_swebench_benchmark.py")
BARE = str(PROJECT_ROOT / "scripts" / "bare_llm_baseline.py")

DASHSCOPE_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
SILICONFLOW_BASE = "https://api.siliconflow.com/v1"
SILICONFLOW_KEY_ENV = "SILICONFLOW_API_KEY"

# ─── Model specs ─────────────────────────────────────────────────────────────
# Each spec = one (model, endpoint, key). The harness runs `full` (agentic,
# REQUIRES tool calling) and `bare` (one-shot, any model) for each.
#
#   key_env         env var holding the API key (injected as OPENAI_API_KEY)
#   full_existing    optional path to a prior full-system JSON to reuse
#   full_disabled    True -> skip `full` (use for models without tool calling)
#
# OpenRouter is OpenAI-compatible, so a single OPENROUTER_API_KEY reaches many
# vendors — ideal for the cross-vendor contamination contrast. Verify current
# model IDs + tool support at:
#   https://openrouter.ai/models?supported_parameters=tools
MODEL_SPECS = [
    # Reference: qwen via DashScope. Likely memorised SWE-bench (the
    # contamination worry incarnate). Reuses the existing 78% full result.
    {
        "tag": "qwen-plus",
        "provider": "openai",
        "model": "qwen-plus",
        "api_base": DASHSCOPE_BASE,
        "key_env": "DASHSCOPE_KEY",
        "full_existing": "results/swebench_benchmark.json",
    },
    # ── OpenRouter (different vendors, one key) ──────────────────────────────
    # Different vendor, tool-capable, cheap.
    {"tag": "gpt-4o-mini", "provider": "openai",
     "model": "openai/gpt-4o-mini",
     "api_base": OPENROUTER_BASE, "key_env": OPENROUTER_KEY_ENV},
    # Different lab, strong, tool-capable.
    {"tag": "deepseek-v3", "provider": "openai",
     "model": "deepseek/deepseek-chat",
     "api_base": OPENROUTER_BASE, "key_env": OPENROUTER_KEY_ENV},
    # WEAK open model — the contamination-contrast STAR: if its `bare` is low
    # (it can't regurgitate) yet `full` lifts it, that lift cannot be
    # memorisation. SiliconFlow is OpenAI-compatible; tool calling verified
    # 2026-07-13. Cheapest option ($0.05/M in+out, Qwen2.5 = no thinking-mode
    # inflation). Published localization reference: LocAgent Qwen2.5-7B
    # (fine-tuned) = 70.8/84.7/88.3 Acc@1/3/5 on 274 SWE-bench Lite.
    {"tag": "qwen3-coder-30b", "provider": "openai",
     "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
     "api_base": SILICONFLOW_BASE, "key_env": SILICONFLOW_KEY_ENV,
     # NOTE: tried Qwen2.5-7B-Instruct first (cheapest, $0.05/M) but it CANNOT
     # do agentic tool-calling — with tool_choice=auto it emits degenerate
     # pseudo-tool-call text and loops until timeout. Qwen3-Coder-30B-A3B (MoE,
     # 3B active) produces clean structured tool_calls and is the cheapest
     # VIABLE option. No thinking-mode inflation (verified).
     # Corporate DNS intermittently returns :: for api.siliconflow.com; pin two
     # alive IPs (verified 2026-07-13) so workers don't stall on resolution.
     "force_ip": "api.siliconflow.com:47.90.171.203,47.85.102.97"},
]


def resolve_key(spec: dict) -> str | None:
    """Resolve a model's API key. Falls back to OPENAI_API_KEY for qwen."""
    val = os.getenv(spec["key_env"])
    if not val and spec["key_env"] == "DASHSCOPE_KEY":
        val = os.getenv("OPENAI_API_KEY")  # current .env stores the dashscope key here
    return val


def build_env(spec: dict) -> dict:
    """Environment for the subprocess: model config overridden per spec."""
    env = os.environ.copy()
    env["LLM_PROVIDER"] = spec["provider"]
    env["LLM_MODEL"] = spec["model"]
    env["LLM_API_BASE"] = spec["api_base"]  # empty string -> client omits base_url
    if spec.get("force_ip"):
        env["LLM_FORCE_IP"] = spec["force_ip"]
    key = resolve_key(spec)
    if key:
        env["OPENAI_API_KEY"] = key
    return env


def output_path(tag: str, mode: str) -> Path:
    return OUT_DIR / f"{mode}_{tag}"  # .json appended by the runner


def run_full(spec: dict, limit: int | None, workers: int) -> dict | None:
    """Run the full agentic system for a model. Returns parsed metrics or None."""
    out_base = output_path(spec["tag"], "full")
    json_path = Path(f"{out_base}.json")

    # Reuse a pre-existing full-system result if declared and present.
    existing = spec.get("full_existing")
    if existing:
        ex_path = PROJECT_ROOT / existing
        if ex_path.exists():
            print(f"  [full/{spec['tag']}] reuse existing -> {ex_path}")
            json_path.parent.mkdir(parents=True, exist_ok=True)
            if json_path.resolve() != ex_path.resolve():
                shutil.copy2(ex_path, json_path)
            return load_metrics(json_path)

    cmd = [PYTHON, BENCH, "--output", str(out_base), "--workers", str(workers)]
    if limit:
        cmd += ["--limit", str(limit)]
    print(f"  [full/{spec['tag']}] running: {' '.join(cmd[1:])}")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=build_env(spec))
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"  [full/{spec['tag']}] FAILED (exit {proc.returncode}) after {dt:.0f}s")
        return None
    return load_metrics(json_path)


def run_bare(spec: dict, limit: int | None, workers: int) -> dict | None:
    """Run the bare-LLM baseline for a model."""
    out_base = output_path(spec["tag"], "bare")
    json_path = Path(f"{out_base}.json")
    cmd = [PYTHON, BARE, "--output", str(out_base), "--workers", str(workers)]
    if limit:
        cmd += ["--limit", str(limit)]
    print(f"  [bare/{spec['tag']}] running: {' '.join(cmd[1:])}")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=build_env(spec))
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"  [bare/{spec['tag']}] FAILED (exit {proc.returncode}) after {dt:.0f}s")
        return None
    return load_metrics(json_path)


def load_metrics(json_path: Path) -> dict | None:
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"  ! could not parse {json_path}: {e}")
        return None
    m = data.get("metrics", {})
    meta = data.get("metadata", {})
    m.setdefault("model", meta.get("model", "?"))
    return m


def pct(v, default="—"):
    return f"{v*100:.1f}%" if isinstance(v, (int, float)) else default


def build_report(results: dict, limit: int | None) -> str:
    """results: {tag: {"full": metrics|None, "bare": metrics|None}}"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Cross-Model Contamination Study — SWE-bench Lite\n",
        f"**Generated:** {ts}  ",
        f"**Instances:** {'all checked-out' if not limit else limit}  ",
        f"**Models:** {', '.join(results.keys())}\n",
        "Each model is run two ways on the *same* instance set: **full** (complete",
        "agentic system) vs **bare** (one LLM call: bug report + file tree, no agents).",
        "Δ(full−bare) is the framework's marginal contribution on top of what the raw",
        "model already knows — the number that survives 'the model memorized the",
        "benchmark'. A Δ that holds across models with different memorization profiles",
        "is the real evidence the method works.\n",
        "## Grid\n",
        "| Model | Mode | Top-1 | Top-3 | Top-5 | MRR | MAP |",
        "|:------|:-----|------:|------:|------:|----:|----:|",
    ]
    for tag, modes in results.items():
        for mode in ("full", "bare"):
            m = modes.get(mode)
            if not m:
                lines.append(f"| {tag} | {mode} | — | — | — | — | — |")
            else:
                lines.append(
                    f"| {tag} | {mode} | {pct(m.get('top_1_accuracy'))} "
                    f"| {pct(m.get('top_3_accuracy'))} | {pct(m.get('top_5_accuracy'))} "
                    f"| {m.get('mrr', 0):.4f} | {m.get('map', 0):.4f} |"
                )
    lines.append("")

    # Per-model delta
    lines += [
        "## Δ(full − bare) — framework contribution per model\n",
        "| Model | ΔTop-1 | ΔTop-3 | ΔTop-5 | ΔMRR | n (bare) |",
        "|:------|------:|------:|------:|-----:|----:|",
    ]
    any_delta = False
    for tag, modes in results.items():
        f_m, b_m = modes.get("full"), modes.get("bare")
        if not f_m or not b_m:
            continue
        any_delta = True

        def delta(k):
            fv, bv = f_m.get(k), b_m.get(k)
            if isinstance(fv, (int, float)) and isinstance(bv, (int, float)):
                return f"{(fv - bv) * 100:+.1f}%"
            return "—"

        dmrr = "—"
        if isinstance(f_m.get("mrr"), (int, float)) and isinstance(b_m.get("mrr"), (int, float)):
            dmrr = f"{f_m['mrr'] - b_m['mrr']:+.4f}"
        lines.append(
            f"| {tag} | {delta('top_1_accuracy')} | {delta('top_3_accuracy')} "
            f"| {delta('top_5_accuracy')} | {dmrr} | {b_m.get('total_instances', '?')} |"
        )
    if not any_delta:
        lines.append("| _need both full and bare for at least one model_ | | | | | |")
    lines.append("")

    # Interpretation notes
    lines += [
        "## How to read this\n",
        "- **High bare + small Δ** on a frontier model (qwen-plus) is consistent with",
        "  memorization: the model already 'knew' the answers, so the framework adds",
        "  little *on that model*. This is exactly the contamination worry.\n",
        "- **A weak model (qwen-turbo / gpt-4o-mini) with low bare but a large Δ** is the",
        "  defence: the model could NOT have regurgitated the answer (its bare score is",
        "  low), yet the framework still lifts it substantially — that lift cannot be",
        "  memorization.\n",
        "- **A different-vendor model (gpt-4o-mini) tracking the same pattern** further",
        "  rules out a single vendor's training-data overlap.\n",
        "- Absolute numbers are inflated by contamination and are NOT the headline claim;",
        "  the per-model Δ and its cross-model consistency are.\n",
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Cross-model full/bare comparison harness")
    ap.add_argument("--models", type=str, default=None,
                    help="Comma-separated model tags to run (default: all)")
    ap.add_argument("--modes", type=str, default="full,bare",
                    help="Comma-separated modes: full,bare")
    ap.add_argument("--limit", type=int, default=None,
                    help="Instance cap applied uniformly to every run")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true",
                    help="Skip (model,mode) cells whose output JSON already exists")
    ap.add_argument("--report-only", action="store_true",
                    help="Do not run anything; just rebuild the report from existing JSONs")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    sel_tags = (
        None if not args.models
        else [t.strip() for t in args.models.split(",") if t.strip()]
    )
    specs = [s for s in MODEL_SPECS if (sel_tags is None or s["tag"] in sel_tags)]

    # Key availability check up front.
    print("Model/key availability:")
    for s in specs:
        key = resolve_key(s)
        print(f"  {s['tag']:12s} model={s['model']:12s} base={s['api_base'] or '(default)':40s} key={'OK' if key else 'MISSING'}")
    missing = [s["tag"] for s in specs if not resolve_key(s) and not s.get("full_existing")]
    if missing and not args.report_only:
        print(f"\nMissing API keys for: {missing}")
        for s in specs:
            if s["tag"] in missing:
                hint = " (or OPENAI_API_KEY)" if s["key_env"] == "DASHSCOPE_KEY" else ""
                print(f"  set {s['key_env']}{hint} for {s['tag']}")

    results: dict[str, dict] = {s["tag"]: {} for s in specs}

    if not args.report_only:
        for s in specs:
            tag = s["tag"]
            for mode in modes:
                out_json = Path(f"{output_path(tag, mode)}.json")
                if args.resume and out_json.exists():
                    print(f"  [{mode}/{tag}] resume: {out_json.name} exists, loading")
                    results[tag][mode] = load_metrics(out_json)
                    continue
                if not resolve_key(s):
                    print(f"  [{mode}/{tag}] SKIP (no key)")
                    continue
                if mode == "full" and s.get("full_disabled"):
                    print(f"  [full/{tag}] SKIP (full_disabled: no tool calling)")
                    continue
                if mode == "full":
                    results[tag][mode] = run_full(s, args.limit, args.workers)
                elif mode == "bare":
                    results[tag][mode] = run_bare(s, args.limit, args.workers)
    else:
        # report-only: load whatever exists
        for s in specs:
            for mode in modes:
                results[s["tag"]][mode] = load_metrics(Path(f"{output_path(s['tag'], mode)}.json"))

    # Always try to load any cells not yet populated (e.g. reuse / partial).
    for tag in results:
        for mode in modes:
            if results[tag].get(mode) is None:
                m = load_metrics(Path(f"{output_path(tag, mode)}.json"))
                if m:
                    results[tag][mode] = m

    report = build_report(results, args.limit)
    report_path = OUT_DIR / "cross_model_report.md"
    report_path.write_text(report, encoding="utf-8")
    summary_path = OUT_DIR / "cross_model_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print(report)
    print("=" * 70)
    print(f"📄 Report  -> {report_path}")
    print(f"📦 Summary -> {summary_path}")


if __name__ == "__main__":
    main()

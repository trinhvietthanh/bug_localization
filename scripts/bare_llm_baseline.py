#!/usr/bin/env python3
"""
Bare-LLM baseline for bug localization — NO agents, NO RAG, NO tools, NO graph.

Exactly ONE LLM call per instance:
    bug report (problem_statement) + repository file tree  ->  ranked files

This isolates what the raw LLM "knows on its own" (which, on public benchmarks,
includes whatever it memorized during training) from what the agentic framework
adds on top. It is the control row in the cross-model contamination study.

Scoring reuses the EXACT same metric functions (top_n_accuracy / reciprocal_rank
/ average_precision, incl. _paths_match normalisation) as the full system, so the
full-vs-bare comparison is apples-to-apples.

Output JSON mirrors scripts/run_swebench_benchmark.py's shape (top-level "metrics"
dict) so scripts/run_cross_model.py can parse both uniformly.

Usage:
    python scripts/bare_llm_baseline.py --output results/cross_model/bare_qwen-plus
    python scripts/bare_llm_baseline.py --limit 5 --workers 4
    python scripts/bare_llm_baseline.py --instance-id django__django-11099

Model/provider are read from the environment (LLM_PROVIDER / LLM_MODEL /
LLM_API_BASE / OPENAI_API_KEY), exactly like the rest of the system.
"""

import sys
import json
import time
import re
import argparse
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import config
from data.loader import SWEBenchLoader
from evaluation.metrics import top_n_accuracy, reciprocal_rank, average_precision
from openai import OpenAI

CHECKOUT_DIR = PROJECT_ROOT / "data" / "swebench_checkouts"

# Directories that are pure noise for localisation context.
EXCLUDE_DIR_NAMES = {
    ".git", "__pycache__", ".tox", "build", "dist", "node_modules",
    "site-packages", ".mypy_cache", ".pytest_cache", ".idea", ".vscode",
    "migrations",  # auto-generated; almost never the bug location
}
EXCLUDE_PATH_PARTS = ("egg-info",)
MAX_FILES = 2500  # cap the file tree so it fits in one context window

SYSTEM_PROMPT = """You are an expert software engineer performing bug localization.

You are given:
1. A bug report (GitHub issue / problem statement).
2. The list of Python source files in the repository (repository-relative paths).

Your task: identify the source file(s) most likely to contain the bug described
in the report.

Return ONLY a JSON object, nothing else, in exactly this form:
{"ranked_files": ["relative/path/file.py", "..."]}

Rules:
- List the MOST likely file first.
- Use repository-relative paths EXACTLY as they appear in the file list.
- At most 10 files.
- No explanation, no markdown, no prose — only the JSON object."""


def find_checked_out_instances(dataset_instances: list) -> list[dict]:
    """Match loaded dataset instances with local checkouts (mirrors the benchmark).

    Falls back to the cached base repo + `git ls-tree` at base_commit when the
    per-instance checkout has been cleaned up: the bare baseline only needs the
    file tree, which is identical either way (a fresh checkout contains exactly
    the tracked files at that commit).
    """
    available = []
    if not CHECKOUT_DIR.exists():
        return available
    for instance in dataset_instances:
        repo_safe = instance.repo.replace("/", "__")
        local_path = CHECKOUT_DIR / repo_safe / instance.instance_id
        base_repo = CHECKOUT_DIR / repo_safe / "repo"
        if local_path.exists() and local_path.is_dir():
            available.append({"bug": instance, "repo_path": str(local_path), "from_git": False})
        elif base_repo.exists():
            available.append({"bug": instance, "repo_path": str(base_repo), "from_git": True})
    return available


def build_file_tree(repo_path: str) -> list[str]:
    """Collect repository-relative .py paths, excluding obvious noise."""
    files: list[str] = []
    root = Path(repo_path)
    for p in sorted(root.rglob("*.py")):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = set(rel.split("/"))
        if parts & EXCLUDE_DIR_NAMES:
            continue
        if any(ex in rel for ex in EXCLUDE_PATH_PARTS):
            continue
        files.append(rel)
        if len(files) >= MAX_FILES:
            break
    return files


def build_file_tree_from_git(base_repo: str, commit: str) -> list[str]:
    """Same as build_file_tree, but reads tracked paths at `commit` from the
    cached base repo instead of a working-tree checkout."""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit],
        cwd=base_repo, capture_output=True, text=True, check=True,
    )
    files: list[str] = []
    for rel in sorted(out.stdout.splitlines()):
        if not rel.endswith(".py"):
            continue
        parts = set(rel.split("/"))
        if parts & EXCLUDE_DIR_NAMES:
            continue
        if any(ex in rel for ex in EXCLUDE_PATH_PARTS):
            continue
        files.append(rel)
        if len(files) >= MAX_FILES:
            break
    return files


def parse_ranked_files(text: str) -> list[str]:
    """Robustly extract a ranked file list from the model's raw output."""
    if not text:
        return []
    # 1) Try to find a JSON object with ranked_files / files.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            for key in ("ranked_files", "files", "predicted_files"):
                vals = obj.get(key)
                if isinstance(vals, list) and vals:
                    return [str(v).strip().strip("`").strip() for v in vals if str(v).strip()]
        except Exception:
            pass
    # 2) Fallback: lines that look like .py paths.
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip().strip("`").strip("*- ").strip()
        line = re.sub(r"^\d+[\.\)]\s*", "", line)  # strip "1. " / "1) "
        line = re.sub(r":.*$", "", line)  # strip trailing ": comment"
        if ".py" in line and 1 < len(line) < 200:
            out.append(line.split()[0])
    # de-dup preserve order
    seen, dedup = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            dedup.append(f)
    return dedup[:10]


def predict(client: OpenAI, model: str, problem_statement: str, file_tree: list[str],
            timeout) -> str:
    user_msg = (
        f"Bug report:\n{problem_statement}\n\n"
        f"Repository Python source files ({len(file_tree)}):\n"
        + "\n".join(file_tree)
        + "\n\nReturn the ranked_files JSON object now."
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=1024,
        timeout=timeout,
    )
    return resp.choices[0].message.content or ""


def build_client() -> OpenAI:
    kwargs = {}
    if config.llm.api_key:
        kwargs["api_key"] = config.llm.api_key
    if config.llm.api_base:
        kwargs["base_url"] = config.llm.api_base
    return OpenAI(**kwargs)


def main():
    ap = argparse.ArgumentParser(description="Bare-LLM baseline (no agents/RAG/tools)")
    ap.add_argument("--dataset", default=config.evaluation.dataset_name)
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, help="Max number of bugs to evaluate")
    ap.add_argument("--instance-id", type=str, help="Run a single instance")
    ap.add_argument("--instance-ids-file", type=str, help="File with one instance_id per line")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default="results/cross_model/bare")
    ap.add_argument("--log-level", default="WARNING")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level)

    loader = SWEBenchLoader(dataset_name=args.dataset)
    if args.instance_id:
        single = loader.load_instance(args.instance_id, split=args.split)
        dataset_instances = [single] if single else []
    else:
        dataset_instances = loader.load(split=args.split)
        if args.instance_ids_file:
            wanted = {
                line.strip()
                for line in Path(args.instance_ids_file).read_text().splitlines()
                if line.strip()
            }
            dataset_instances = [i for i in dataset_instances if i.instance_id in wanted]
        if args.limit:
            dataset_instances = dataset_instances[: args.limit]

    bugs = find_checked_out_instances(dataset_instances)
    print(
        f"Bare-LLM baseline | model={config.llm.model} | provider={config.llm.provider} "
        f"| base={config.llm.api_base or '(default)'} | instances={len(bugs)}"
    )
    if not bugs:
        print("No checked-out instances found. Run scripts/checkout_swebench.py first.")
        return

    client = build_client()
    timeout = config.llm_call_timeout if config.llm_call_timeout > 0 else None
    print_lock = threading.Lock()

    def run_one(entry: dict) -> dict:
        bug = entry["bug"]
        repo = entry["repo_path"]
        t0 = time.time()
        err = None
        preds: list[str] = []
        try:
            if entry.get("from_git"):
                tree = build_file_tree_from_git(repo, bug.base_commit)
            else:
                tree = build_file_tree(repo)
            text = predict(client, config.llm.model, bug.problem_statement, tree, timeout)
            preds = parse_ranked_files(text)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        dt = time.time() - t0
        gt = bug.buggy_files
        return {
            "instance_id": bug.instance_id,
            "predicted": preds[:5],
            "ground_truth": gt,
            "top1_hit": top_n_accuracy(preds, gt, 1),
            "top3_hit": top_n_accuracy(preds, gt, 3),
            "top5_hit": top_n_accuracy(preds, gt, 5),
            "rr": reciprocal_rank(preds, gt),
            "ap": average_precision(preds, gt),
            "time": round(dt, 2),
            "llm_calls": 1,
            "error": err,
        }

    per_instance: list[dict] = []
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, e): e for e in bugs}
        done = 0
        for f in as_completed(futs):
            res = f.result()
            per_instance.append(res)
            done += 1
            with print_lock:
                status = "OK " if res["top1_hit"] else ("t3 " if res["top3_hit"] else ("t5 " if res["top5_hit"] else "NO"))
                print(
                    f"  [{done}/{len(bugs)}] {status} {res['instance_id']:30s} "
                    f"RR={res['rr']:.3f} pred={res['predicted'][0] if res['predicted'] else '-'}"
                    + (f"  ERR={res['error']}" if res["error"] else "")
                )

    total_time = time.time() - t_start
    n = len(per_instance) or 1
    metrics = {
        "top_1_accuracy": sum(i["top1_hit"] for i in per_instance) / n,
        "top_3_accuracy": sum(i["top3_hit"] for i in per_instance) / n,
        "top_5_accuracy": sum(i["top5_hit"] for i in per_instance) / n,
        "mrr": sum(i["rr"] for i in per_instance) / n,
        "map": sum(i["ap"] for i in per_instance) / n,
        "total_instances": len(per_instance),
        "instances_with_match": sum(1 for i in per_instance if i["rr"] > 0),
        "total_time_seconds": round(total_time, 2),
        "avg_time_per_instance": round(total_time / n, 2),
    }

    out_base = args.output
    metadata = {
        "dataset": args.dataset,
        "split": args.split,
        "model": config.llm.model,
        "provider": config.llm.provider,
        "api_base": config.llm.api_base or "(default)",
        "mode": "bare_llm",
        "timestamp": datetime.now().isoformat(),
        "limit": args.limit,
    }
    payload = {"metadata": metadata, "metrics": metrics, "per_instance": per_instance}

    out_dir = Path(out_base).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    Path(f"{out_base}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    # Summary CSV (one row per metric) — matches benchmark summary shape.
    lines = ["metric,value"]
    for k, v in metrics.items():
        lines.append(f"{k},{v}")
    Path(f"{out_base}_summary.csv").write_text("\n".join(lines) + "\n")

    print(
        f"\nBare-LLM | {config.llm.model} | Top-1={metrics['top_1_accuracy']*100:.1f}% "
        f"Top-3={metrics['top_3_accuracy']*100:.1f}% Top-5={metrics['top_5_accuracy']*100:.1f}% "
        f"MRR={metrics['mrr']:.4f} | n={metrics['total_instances']}"
    )
    print(f"  -> {out_base}.json")


if __name__ == "__main__":
    main()

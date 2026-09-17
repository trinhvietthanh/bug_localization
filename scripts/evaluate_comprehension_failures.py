#!/usr/bin/env python3
"""Evaluate ComprehensionAgent on persistent SWE-bench localization failures.

The cohort is the intersection of Top-5 failures from two prior full runs. This
isolates repeatable architecture failures from stochastic ranking failures and
runs only Phase 1, making candidate-recall attribution explicit and affordable.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.base_agent import AgentContext
from agents.comprehension import ComprehensionAgent
from config import config
from data.loader import SWEBenchLoader
from data.preprocessor import BugReportPreprocessor
from tools.repo_skeleton import generate_repo_skeleton

CHECKOUT_DIR = PROJECT_ROOT / "data" / "swebench_checkouts"


def persistent_failure_ids(run_a: Path, run_b: Path) -> list[str]:
    def failed(path: Path) -> set[str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            row["instance_id"]
            for row in data.get("per_instance", [])
            if not row.get("top5_hit", False)
        }

    return sorted(failed(run_a) & failed(run_b))


def build_context(bug, repo_path: Path) -> AgentContext:
    processed = BugReportPreprocessor().process(bug)
    hints = list(processed.mentioned_files) + list(processed.keywords[:15])
    skeleton = generate_repo_skeleton(
        repo_path=str(repo_path),
        language="python",
        max_files=config.repo_skeleton_max_files,
        priority_hints=hints,
    )
    return AgentContext(
        instance_id=bug.instance_id,
        repo_id=bug.repo.replace("/", "__"),
        problem_statement=bug.problem_statement,
        repo_path=str(repo_path.resolve()),
        error_messages=processed.error_messages,
        stack_traces=processed.stack_traces,
        mentioned_files=processed.mentioned_files,
        mentioned_functions=(
            list(processed.mentioned_functions)
            + list(processed.stack_trace_methods)
        ),
        keywords=processed.keywords,
        language="python",
        file_extension="*.py",
        repo_skeleton=skeleton,
        candidate_methods=list(processed.stack_trace_methods),
        log_parse_result=processed.log_parse_result,
    )


def evaluate_one(bug) -> dict:
    repo_safe = bug.repo.replace("/", "__")
    repo_path = CHECKOUT_DIR / repo_safe / bug.instance_id
    if not repo_path.is_dir():
        return {
            "instance_id": bug.instance_id,
            "success": False,
            "error": f"Missing checkout: {repo_path}",
        }

    context = build_context(bug, repo_path)
    agent = ComprehensionAgent()
    started = time.perf_counter()
    result = agent.run(context)
    elapsed = time.perf_counter() - started
    if result.success:
        agent.process_result(result, context)

    gold = list(bug.buggy_files)
    predicted = list(context.candidate_files)
    rank = next(
        (index for index, path in enumerate(predicted, 1) if path in gold),
        0,
    )
    return {
        "instance_id": bug.instance_id,
        "success": result.success,
        "error": result.error or None,
        "predicted": predicted,
        "ground_truth": gold,
        "gold_rank": rank,
        "top1_hit": rank == 1,
        "top3_hit": 0 < rank <= 3,
        "top5_hit": 0 < rank <= 5,
        "candidate_recall": rank > 0,
        "candidate_methods": list(context.candidate_methods),
        "hypothesis": context.fault_hypothesis,
        "patch_owner_files": result.output.get("patch_owner_files", []),
        "patch_owner_audit": result.output.get("patch_owner_audit"),
        "llm_calls": result.num_llm_calls,
        "tool_calls": result.num_tool_calls,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "elapsed_seconds": round(elapsed, 2),
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    if not total:
        return {"total": 0}
    return {
        "total": total,
        "successful": sum(bool(row.get("success")) for row in rows),
        "candidate_recall": sum(bool(row.get("candidate_recall")) for row in rows) / total,
        "top_1_accuracy": sum(bool(row.get("top1_hit")) for row in rows) / total,
        "top_3_accuracy": sum(bool(row.get("top3_hit")) for row in rows) / total,
        "top_5_accuracy": sum(bool(row.get("top5_hit")) for row in rows) / total,
        "llm_calls": sum(row.get("llm_calls", 0) for row in rows),
        "tool_calls": sum(row.get("tool_calls", 0) for row in rows),
        "total_tokens": sum(row.get("total_tokens", 0) for row in rows),
        "elapsed_seconds_sum": round(
            sum(row.get("elapsed_seconds", 0) for row in rows), 2
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-a",
        type=Path,
        default=PROJECT_ROOT / "results/swebench_300_e123_qwen.json",
    )
    parser.add_argument(
        "--run-b",
        type=Path,
        default=PROJECT_ROOT / "results/swebench_300_v2.json",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "results/comprehension_persistent_failures.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ids = persistent_failure_ids(args.run_a, args.run_b)
    if args.limit:
        ids = ids[: args.limit]

    bugs = {
        bug.instance_id: bug
        for bug in SWEBenchLoader().load()
        if bug.instance_id in ids
    }
    selected = [bugs[instance_id] for instance_id in ids if instance_id in bugs]
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(evaluate_one, bug): bug for bug in selected}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                f"{row['instance_id']}: success={row.get('success')} "
                f"rank={row.get('gold_rank', 0)} "
                f"calls={row.get('llm_calls', 0)} "
                f"tokens={row.get('total_tokens', 0)}"
            )

    rows.sort(key=lambda row: row["instance_id"])
    payload = {
        "metadata": {
            "model": config.llm.model,
            "provider": config.llm.provider,
            "cohort": "persistent Top-5 failures in both source runs",
            "run_a": str(args.run_a),
            "run_b": str(args.run_b),
        },
        "metrics": summarize(rows),
        "per_instance": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(payload["metrics"], indent=2))
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

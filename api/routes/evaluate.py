"""
Evaluation API routes.
"""

import os
import json
import asyncio
from typing import Optional, List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

router = APIRouter()

executor = ThreadPoolExecutor(max_workers=2)

eval_jobs: dict[str, dict] = {}


class EvaluateRequest(BaseModel):
    benchmark: str = "defects4j"
    project: Optional[str] = None
    limit: Optional[int] = None
    use_graph_rag: bool = True
    output_path: Optional[str] = None


class EvaluateResponse(BaseModel):
    job_id: str
    status: str


class MetricsSummary(BaseModel):
    top_1: float
    top_3: float
    top_5: float
    top_10: float
    mrr: float
    map: float
    total_instances: int
    successful_instances: int


class EvaluateResult(BaseModel):
    job_id: str
    status: str
    metrics: Optional[MetricsSummary] = None
    results: Optional[List[dict]] = None
    error: Optional[str] = None


def run_evaluation(job_id: str, request: EvaluateRequest):
    try:
        from config import config
        from evaluation.evaluator import BenchmarkEvaluator
        from data.defects4j_loader import Defects4JLoader
        from evaluation.metrics import top_n_accuracy, mrr, mean_average_precision

        config.enable_graph_rag = request.use_graph_rag

        loader = Defects4JLoader()
        instances = loader.load_project(request.project or "Lang")
        if request.limit:
            instances = instances[: request.limit]

        from agents.orchestrator import Orchestrator

        orchestrator = Orchestrator()

        results = []
        for inst in instances:
            repo_path = f"data/defects4j_checkouts/{request.project}/{inst.instance_id}"
            if not os.path.exists(repo_path):
                continue

            result = orchestrator.localize(inst, repo_path=repo_path, verbose=False)
            hit_1 = top_n_accuracy(result.ranked_files, inst.buggy_files, 1)
            hit_3 = top_n_accuracy(result.ranked_files, inst.buggy_files, 3)
            hit_5 = top_n_accuracy(result.ranked_files, inst.buggy_files, 5)
            hit_10 = top_n_accuracy(result.ranked_files, inst.buggy_files, 10)

            results.append(
                {
                    "instance_id": inst.instance_id,
                    "ranked_files": result.ranked_files[:10],
                    "ground_truth": inst.buggy_files,
                    "hit@1": hit_1,
                    "hit@3": hit_3,
                    "hit@5": hit_5,
                    "hit@10": hit_10,
                    "time": result.total_time,
                }
            )

        total = len(results)
        if total > 0:
            top_1 = sum(r["hit@1"] for r in results) / total
            top_3 = sum(r["hit@3"] for r in results) / total
            top_5 = sum(r["hit@5"] for r in results) / total
            top_10 = sum(r["hit@10"] for r in results) / total
            mrr_val = (
                sum(
                    1 / (i + 1)
                    for r in results
                    for i, f in enumerate(r["ranked_files"])
                    if f in r["ground_truth"]
                )
                / total
            )
        else:
            top_1 = top_3 = top_5 = top_10 = mrr_val = 0

        eval_jobs[job_id] = {
            "status": "completed",
            "metrics": {
                "top_1": top_1,
                "top_3": top_3,
                "top_5": top_5,
                "top_10": top_10,
                "mrr": mrr_val,
                "map": mrr_val,
                "total_instances": total,
                "successful_instances": total,
            },
            "results": results,
        }
    except Exception as e:
        import traceback

        eval_jobs[job_id] = {
            "status": "failed",
            "error": f"{str(e)}\n{traceback.format_exc()}",
        }


@router.post("", response_model=EvaluateResponse)
async def start_evaluate(request: EvaluateRequest):
    import uuid

    job_id = str(uuid.uuid4())[:8]
    eval_jobs[job_id] = {"status": "running"}

    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, run_evaluation, job_id, request)

    return EvaluateResponse(job_id=job_id, status="running")


@router.get("/{job_id}", response_model=EvaluateResult)
async def get_evaluate_result(job_id: str):
    if job_id not in eval_jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = eval_jobs[job_id]
    return EvaluateResult(
        job_id=job_id,
        status=job["status"],
        metrics=job.get("metrics"),
        results=job.get("results"),
        error=job.get("error"),
    )

"""
Localization API routes.
"""

import os
import asyncio
from typing import Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from config import config

router = APIRouter()

executor = ThreadPoolExecutor(max_workers=4)

jobs: dict[str, dict] = {}


class LocalizeRequest(BaseModel):
    bug_report: str
    repo_path: str
    use_graph_rag: bool = True
    multi_pass: int = 1
    reflection_rounds: Optional[int] = None
    reflection_threshold: Optional[float] = None


class LocalizeResponse(BaseModel):
    job_id: str
    status: str


class LocalizeResult(BaseModel):
    job_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


def run_localization(job_id: str, request: LocalizeRequest):
    try:
        from agents.orchestrator import Orchestrator
        from data.loader import BugInstance

        config.enable_graph_rag = request.use_graph_rag

        retriever = None
        try:
            from commands.localize import _make_retriever

            retriever = _make_retriever()
        except Exception:
            pass

        orchestrator = Orchestrator(retriever=retriever)

        instance = BugInstance(
            instance_id="manual",
            repo="local",
            problem_statement=request.bug_report,
            base_commit="",
            patch="",
            test_patch="",
        )

        result = orchestrator.localize(
            instance,
            repo_path=request.repo_path,
            verbose=False,
            reflection_max_rounds=request.reflection_rounds,
            reflection_conf_threshold=request.reflection_threshold,
        )

        jobs[job_id] = {
            "status": "completed",
            "result": result.to_dict(),
        }
    except Exception as e:
        jobs[job_id] = {
            "status": "failed",
            "error": str(e),
        }


@router.post("", response_model=LocalizeResponse)
async def start_localize(request: LocalizeRequest, background_tasks: BackgroundTasks):
    if not os.path.exists(request.repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist")

    import uuid

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "result": None}

    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, run_localization, job_id, request)

    return LocalizeResponse(job_id=job_id, status="running")


@router.get("/{job_id}", response_model=LocalizeResult)
async def get_localize_result(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return LocalizeResult(
        job_id=job_id,
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
    )

"""
Benchmark data API routes.
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class BugInstance(BaseModel):
    instance_id: str
    project: str
    problem_statement: str
    buggy_files: List[str]


class ProjectInfo(BaseModel):
    name: str
    bug_count: int
    language: str


@router.get("/projects", response_model=List[ProjectInfo])
async def list_projects(benchmark: str = "defects4j"):
    try:
        if benchmark == "defects4j":
            return [
                ProjectInfo(name="Lang", bug_count=65, language="Java"),
                ProjectInfo(name="Math", bug_count=106, language="Java"),
                ProjectInfo(name="Chart", bug_count=26, language="Java"),
                ProjectInfo(name="Closure", bug_count=133, language="Java"),
                ProjectInfo(name="Mockito", bug_count=38, language="Java"),
                ProjectInfo(name="Time", bug_count=27, language="Java"),
            ]
        else:
            raise HTTPException(
                status_code=400, detail=f"Unknown benchmark: {benchmark}"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bugs", response_model=List[BugInstance])
async def list_bugs(
    benchmark: str = "defects4j", project: Optional[str] = None, limit: int = 20
):
    try:
        bugs = []

        if benchmark == "defects4j":
            from data.defects4j_loader import Defects4JLoader

            loader = Defects4JLoader()
            instances = loader.load_project(project or "Lang")
            for inst in instances[:limit]:
                bugs.append(
                    BugInstance(
                        instance_id=inst.instance_id,
                        project=project or "Lang",
                        problem_statement=inst.problem_statement[:500],
                        buggy_files=inst.buggy_files,
                    )
                )
        else:
            raise HTTPException(
                status_code=400, detail=f"Unknown benchmark: {benchmark}"
            )

        return bugs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bugs/{instance_id}", response_model=BugInstance)
async def get_bug(instance_id: str, benchmark: str = "defects4j"):
    try:
        if benchmark == "defects4j":
            from data.defects4j_loader import Defects4JLoader

            loader = Defects4JLoader()
            inst = loader.load_instance(instance_id)
            if not inst:
                raise HTTPException(status_code=404, detail="Bug instance not found")
            return BugInstance(
                instance_id=inst.instance_id,
                project=instance_id.split("_")[0],
                problem_statement=inst.problem_statement,
                buggy_files=inst.buggy_files,
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"Unknown benchmark: {benchmark}"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

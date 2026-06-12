"""
FastAPI backend for Bug Localization UI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api.routes import localize, evaluate, graph, benchmarks


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Bug Localization API",
    description="API for the Bug Localization System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(localize.router, prefix="/api/localize", tags=["localize"])
app.include_router(evaluate.router, prefix="/api/evaluate", tags=["evaluate"])
app.include_router(graph.router, prefix="/api/graph", tags=["graph"])
app.include_router(benchmarks.router, prefix="/api/benchmarks", tags=["benchmarks"])


@app.get("/health")
async def health():
    return {"status": "ok"}

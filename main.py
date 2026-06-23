"""
Vibra Score — Backend FastAPI
MVP com SQLite + AWS S3
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import os

from models.database import init_db
from routers import documents, analysis, reports, grupos, overrides, scoring


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Vibra Score API",
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

# Routers
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(analysis.router,  prefix="/api/analysis",  tags=["analysis"])
app.include_router(reports.router,   prefix="/api/reports",   tags=["reports"])
app.include_router(grupos.router,    prefix="/api/grupos",    tags=["grupos"])      # Rodada P
app.include_router(overrides.router, prefix="/api/overrides", tags=["overrides"])   # Etapa 2
# ETAPA 5 — Calibração de score (já tem prefix /api/scoring no próprio router)
app.include_router(scoring.router)

# Serve frontend
app.mount("/static", StaticFiles(directory="../frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("../frontend/index.html", media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok"}

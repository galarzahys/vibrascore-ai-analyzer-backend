"""
Vibra Score — Backend FastAPI
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from models.database import init_db
from routers import documents, analysis, reports, grupos, overrides, scoring, auth, admin, clients


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Vibra Score API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(analysis.router,  prefix="/api/analysis",  tags=["analysis"])
app.include_router(reports.router,   prefix="/api/reports",   tags=["reports"])
app.include_router(grupos.router,    prefix="/api/grupos",    tags=["grupos"])
app.include_router(overrides.router, prefix="/api/overrides", tags=["overrides"])
app.include_router(scoring.router)
app.include_router(auth.router,      prefix="/api/auth",      tags=["auth"])
app.include_router(admin.router,     prefix="/api/admin",     tags=["admin"])
app.include_router(clients.router,   prefix="/api/clientes",  tags=["clientes"])

app.mount("/static", StaticFiles(directory="../frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("../frontend/index.html", media_type="text/html")


@app.get("/health")
async def health():
    return {"status": "ok"}

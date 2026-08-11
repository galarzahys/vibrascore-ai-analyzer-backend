"""
routers/ia_run.py
Endpoint principal de execução de análise IA.
Recebe contexto completo do .NET, processa em background,
notifica via webhook e mantém estado em SQLite local.
"""

import os
import json
import uuid
import asyncio
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Body, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.database import get_db
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

INTERNAL_SECRET  = os.getenv("INTERNAL_SECRET", "978e6315-c667-45b5-bae8-b8b64578387e")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://api.vibrascore.com.br")
#WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "http://localhost:7116")
JOB_TIMEOUT_MIN  = 30
JOB_TTL_DAYS     = 7


# ── Modelos ───────────────────────────────────────────────────────

class DocumentInput(BaseModel):
    field_key:     str
    field_label:   Optional[str] = None
    s3_key:        Optional[str] = None
    original_name: Optional[str] = None
    is_required:   bool = True
    is_valid:      Optional[bool] = None


class RunAnalysisRequest(BaseModel):
    analysis_id:            str
    company_name:           Optional[str] = None
    cnpj:                   Optional[str] = None
    analyst_name:           Optional[str] = None
    client_id:              Optional[int] = None
    package_level:          str = "full"
    historico_interno:      Optional[str] = None
    faturamento_manual_json: Optional[str] = None
    documents:              list[DocumentInput] = []
    diretrizes:             Optional[str] = None


# ── DB helpers ────────────────────────────────────────────────────

def _criar_tabela_jobs(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS ia_jobs (
            job_id      TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'processing',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            result_json TEXT,
            error_msg   TEXT,
            webhook_ok  INTEGER DEFAULT 0
        )
    """))
    db.commit()


def _criar_job(db: Session, job_id: str, analysis_id: str) -> None:
    now = datetime.utcnow().isoformat()
    exp = (datetime.utcnow() + timedelta(days=JOB_TTL_DAYS)).isoformat()
    db.execute(text("""
        INSERT INTO ia_jobs (job_id, analysis_id, status, created_at, updated_at, expires_at)
        VALUES (:jid, :aid, 'processing', :now, :now, :exp)
    """), {"jid": job_id, "aid": analysis_id, "now": now, "exp": exp})
    db.commit()


def _atualizar_job(db: Session, job_id: str, status: str,
                   result_json: str | None = None, error_msg: str | None = None) -> None:
    now = datetime.utcnow().isoformat()
    db.execute(text("""
        UPDATE ia_jobs SET status=:s, updated_at=:now,
            result_json=:rj, error_msg=:em
        WHERE job_id=:jid
    """), {"s": status, "now": now, "rj": result_json, "em": error_msg, "jid": job_id})
    db.commit()


def _marcar_webhook_ok(db: Session, job_id: str) -> None:
    db.execute(text("UPDATE ia_jobs SET webhook_ok=1 WHERE job_id=:jid"), {"jid": job_id})
    db.commit()


def _limpar_jobs_expirados(db: Session) -> None:
    now = datetime.utcnow().isoformat()
    db.execute(text("DELETE FROM ia_jobs WHERE expires_at < :now"), {"now": now})
    db.commit()


# ── Endpoint: POST /run ───────────────────────────────────────────

@router.post("/run")
async def run_analysis(
    background_tasks: BackgroundTasks,
    body: RunAnalysisRequest,
    db: Session = Depends(get_db),
):
    """
    Recebe contexto completo do .NET e dispara análise IA em background.
    Retorna job_id imediatamente para polling/webhook.
    """
    _criar_tabela_jobs(db)
    _limpar_jobs_expirados(db)

    job_id = str(uuid.uuid4())
    _criar_job(db, job_id, body.analysis_id)

    background_tasks.add_task(_processar_analise, job_id, body)

    return {
        "job_id":      job_id,
        "analysis_id": body.analysis_id,
        "status":      "processing",
    }


# ── Endpoint: GET /status/{job_id} ───────────────────────────────

@router.get("/status/{job_id}")
async def get_status(job_id: str, db: Session = Depends(get_db)):
    """
    Polling fallback — retorna status e resultado do job.
    """
    _criar_tabela_jobs(db)
    row = db.execute(
        text("SELECT status, result_json, error_msg FROM ia_jobs WHERE job_id=:jid"),
        {"jid": job_id}
    ).fetchone()

    if not row:
        raise HTTPException(404, "Job não encontrado")

    result = {
        "job_id": job_id,
        "status": row[0],
    }
    if row[0] == "done" and row[1]:
        result["result"] = json.loads(row[1])
    if row[0] == "error":
        result["error"] = row[2]

    return result


# ── Endpoint: GET /status/analysis/{analysis_id} ─────────────────

@router.get("/status/analysis/{analysis_id}")
async def get_status_by_analysis(analysis_id: str, db: Session = Depends(get_db)):
    """
    Polling por analysis_id (alternativa ao job_id).
    """
    _criar_tabela_jobs(db)
    row = db.execute(
        text("""SELECT job_id, status, result_json, error_msg
                FROM ia_jobs WHERE analysis_id=:aid
                ORDER BY created_at DESC LIMIT 1"""),
        {"aid": analysis_id}
    ).fetchone()

    if not row:
        raise HTTPException(404, "Nenhum job encontrado para este analysis_id")

    result = {"job_id": row[0], "analysis_id": analysis_id, "status": row[1]}
    if row[1] == "done" and row[2]:
        result["result"] = json.loads(row[2])
    if row[1] == "error":
        result["error"] = row[3]
    return result


# ── Background: processar análise ────────────────────────────────

def _processar_analise(job_id: str, body: RunAnalysisRequest) -> None:
    from models.database import SessionLocal
    db = SessionLocal()
    try:
        print(f"[JOB {job_id[:8]}] Iniciando análise {body.analysis_id[:8]}", flush=True)

        # criar objetos de documento compatíveis com run_full_analysis
        from dataclasses import dataclass
        from typing import Optional as Opt

        @dataclass
        class DocProxy:
            field_key:     str
            field_label:   Opt[str]
            s3_key:        Opt[str]
            original_name: str
            is_valid:      Opt[bool]
            is_required:   bool

        docs = [DocProxy(
            field_key     = d.field_key,
            field_label   = d.field_label,
            s3_key        = d.s3_key,
            original_name = d.original_name or '',
            is_valid      = d.is_valid,
            is_required   = d.is_required,
        ) for d in body.documents]

        # criar registro temporário na DB para que run_full_analysis
        # possa ler historico_interno e faturamento_manual_json
        from models.database import Analysis
        existing = db.query(Analysis).filter(Analysis.id == body.analysis_id).first()
        if not existing:
            tmp = Analysis(
                id                    = body.analysis_id,
                status                = 'processing',
                company_name          = body.company_name,
                cnpj                  = body.cnpj,
                analyst_name          = body.analyst_name,
                client_id             = str(body.client_id) if body.client_id else None,
                historico_interno     = body.historico_interno,
                faturamento_manual_json = body.faturamento_manual_json,
            )
            db.add(tmp)
            db.commit()
        else:
            # actualizar campos que pueden haber cambiado
            existing.historico_interno      = body.historico_interno
            existing.faturamento_manual_json = body.faturamento_manual_json
            db.commit()

        # llamar al motor original — reusar TODO el prompt y la lógica
        from services.analysis_service import run_full_analysis
        run_full_analysis(
            analysis_id = body.analysis_id,
            documents   = docs,
            db          = db,
            diretrizes  = body.diretrizes or "",
        )

        # leer resultado guardado por run_full_analysis
        from models.database import Report
        report = db.query(Report).filter(Report.analysis_id == body.analysis_id).first()
        result_json = report.raw_json if report else '{}'

        _atualizar_job(db, job_id, "done", result_json=result_json)
        print(f"[JOB {job_id[:8]}] Concluído OK", flush=True)

        _disparar_webhook(job_id, body.analysis_id, result_json, db)

    except Exception as e:
        import traceback
        print(f"[JOB {job_id[:8]}] ERRO: {e}", flush=True)
        traceback.print_exc()
        _atualizar_job(db, job_id, "error", error_msg=str(e))
        _disparar_webhook_erro(job_id, body.analysis_id, str(e))
    finally:
        db.close()


def _extrair_textos(documents: list[DocumentInput]) -> dict:
    """Baixa e extrai texto de cada documento do S3."""
    import pdfplumber, io
    from services.s3_service import download_file

    texts: dict[str, list[str]] = {}
    for doc in documents:
        if not doc.s3_key or doc.s3_key.startswith("manual:"):
            continue
        try:
            if doc.s3_key.startswith("local:"):
                path = doc.s3_key[6:]
                with open(path, "rb") as f:
                    file_bytes = f.read()
            else:
                file_bytes = download_file(doc.s3_key)

            # JSON de integração — ler direto
            if doc.s3_key.endswith(".json") or doc.mime_type == "application/json":
                text = file_bytes.decode("utf-8", errors="ignore")
            else:
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    text = "\n".join(p.extract_text() or "" for p in pdf.pages)

            if text.strip():
                if doc.field_key not in texts:
                    texts[doc.field_key] = []
                texts[doc.field_key].append(text[:10000])

        except Exception as e:
            print(f"[EXTRATOR] Erro ao processar {doc.field_key}: {e}", flush=True)

    return {k: "\n\n---\n\n".join(v) for k, v in texts.items()}


def _disparar_webhook(job_id: str, analysis_id: str, result_json: str, db: Session) -> None:
    """Notifica o .NET que o job foi concluído com sucesso."""
    url = f"{WEBHOOK_BASE_URL}/api/WebHooks/analyzer/{job_id}"
    headers = {"X-Internal-Secret": INTERNAL_SECRET, "Content-Type": "application/json"}
    payload = {
        "job_id":      job_id,
        "analysis_id": analysis_id,
        "status":      "done",
        "result":      json.loads(result_json),
    }
    for tentativa in range(1, 4):
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=30)
            if r.status_code < 400:
                _marcar_webhook_ok(db, job_id)
                print(f"[WEBHOOK {job_id[:8]}] URL: {url} OK (tentativa {tentativa})", flush=True)
                return
            print(f"[WEBHOOK {job_id[:8]}] URL: {url} HTTP {r.status_code} (tentativa {tentativa})", flush=True)
        except Exception as e:
            print(f"[WEBHOOK {job_id[:8]}] Erro tentativa {tentativa}: {e}", flush=True)
        import time; time.sleep(5 * tentativa)
    print(f"[WEBHOOK {job_id[:8]}] Falhou após 3 tentativas — .NET fará polling", flush=True)


def _disparar_webhook_erro(job_id: str, analysis_id: str, error_msg: str) -> None:
    """Notifica o .NET que o job falhou."""
    url = f"{WEBHOOK_BASE_URL}/api/WebHooks/analyzer/{job_id}"
    headers = {"X-Internal-Secret": INTERNAL_SECRET, "Content-Type": "application/json"}
    payload = {"job_id": job_id, "analysis_id": analysis_id, "status": "error", "error": error_msg}
    try:
        httpx.post(url, json=payload, headers=headers, timeout=15)
    except Exception:
        pass

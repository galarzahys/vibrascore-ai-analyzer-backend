"""
routers/reanalise.py — v2
Re-análise cria um novo Analysis vinculado ao original via parent_analysis_id.
"""
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.database import get_db, Analysis, Document, Report

router = APIRouter()

# ── CONFIGURAÇÃO ──────────────────────────────────────────────────
MAX_REANALISES = 5  # máximo de re-análises por análise original


# ── LISTAR VERSÕES VINCULADAS ─────────────────────────────────────

@router.get("/{analysis_id}/versoes")
async def listar_versoes(analysis_id: str, db: Session = Depends(get_db)):
    """
    Retorna todas as versões vinculadas a um análise.
    Busca pelo id original — seja o próprio ou via parent_analysis_id.
    """
    # descobrir o id original
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    origem_id = analysis.parent_analysis_id or analysis.id

    # buscar o original + todos os re-análises
    rows = db.execute(
        text("""
            SELECT id, company_name, cnpj, vibra_id, vibra_ver,
                   version_num, status, created_at, parent_analysis_id
            FROM analyses
            WHERE id = :origem OR parent_analysis_id = :origem
            ORDER BY version_num ASC
        """),
        {"origem": origem_id}
    ).fetchall()

    return [{
        "id": r[0],
        "company_name": r[1],
        "cnpj": r[2],
        "vibra_id": r[3],
        "vibra_ver": r[4],
        "version_num": r[5] or 1,
        "status": r[6],
        "created_at": r[7],
        "is_current": r[0] == analysis_id,
        "is_original": r[8] is None,
    } for r in rows]


# ── LISTAR DOCUMENTOS DISPONÍVEIS ────────────────────────────────

@router.get("/{analysis_id}/documentos")
async def listar_docs(analysis_id: str, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(
        Document.analysis_id == analysis_id
    ).order_by(Document.field_key).all()

    return [{
        "id": d.id,
        "field_key": d.field_key,
        "field_label": d.field_label or d.field_key,
        "original_name": d.original_name,
        "is_valid": d.is_valid,
        "is_required": d.is_required,
        "s3_key": d.s3_key,
        "incluir": True,
    } for d in docs]


# ── DISPARAR RE-ANÁLISE ───────────────────────────────────────────

@router.post("/{analysis_id}/run")
async def run_reanalise(
    analysis_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    doc_ids: list[str] = Body(..., embed=True),
    created_by: str = Body(default="", embed=True),
    client_id: int = Body(default=None, embed=True),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    # verificar limite de re-análises
    origem_id = analysis.parent_analysis_id or analysis.id
    count = db.execute(
        text("SELECT COUNT(*) FROM analyses WHERE parent_analysis_id = :origem"),
        {"origem": origem_id}
    ).scalar()

    if count >= MAX_REANALISES:
        raise HTTPException(400, f"Limite de {MAX_REANALISES} re-análises atingido para esta análise.")

    # próximo version_num
    max_ver = db.execute(
        text("""
            SELECT MAX(version_num) FROM analyses
            WHERE id = :origem OR parent_analysis_id = :origem
        """),
        {"origem": origem_id}
    ).scalar() or 1
    next_version = max_ver + 1

    # criar novo Analysis vinculado
    novo_id = str(uuid.uuid4())
    novo_analysis = Analysis(
        id=novo_id,
        status="processing",
        company_name=analysis.company_name,
        cnpj=analysis.cnpj,
        analyst_name=analysis.analyst_name,
        package_level=analysis.package_level,
        vibra_id=analysis.vibra_id,
        vibra_ver=next_version,
        client_id=client_id or analysis.client_id,
        parent_analysis_id=origem_id,
        version_num=next_version,
    )
    db.add(novo_analysis)
    db.commit()

    # disparar em background
    background_tasks.add_task(
        _run_reanalise_background,
        novo_id,
        analysis_id,
        doc_ids,
    )

    return {
        "novo_analysis_id": novo_id,
        "version_num": next_version,
        "status": "processing",
    }


# ── BACKGROUND TASK ───────────────────────────────────────────────

def _run_reanalise_background(
    novo_analysis_id: str,
    analysis_id_original: str,
    doc_ids: list[str],
):
    from sqlalchemy.orm import Session as DBSession
    from models.database import SessionLocal, Document, Analysis
    from sqlalchemy import text as sa_text
    import asyncio

    db: DBSession = SessionLocal()
    try:
        # buscar documentos selecionados do análise original
        docs = db.query(Document).filter(
            Document.id.in_(doc_ids),
            Document.analysis_id == analysis_id_original
        ).all()

        print(f"[REANALISE] doc_ids recibidos: {doc_ids}", flush=True)
        print(f"[REANALISE] docs encontrados: {[(d.id[:8], d.field_key, d.original_name) for d in docs]}", flush=True)

        # copiar documentos para o novo análise
        import uuid as _uuid
        novos_docs = []
        for d in docs:
            novo_doc = Document(
                id=str(_uuid.uuid4()),
                analysis_id=novo_analysis_id,
                field_key=d.field_key,
                field_label=d.field_label,
                original_name=d.original_name,
                s3_key=d.s3_key,
                file_size=d.file_size,
                mime_type=d.mime_type,
                is_valid=d.is_valid,
                validation_msg=d.validation_msg,
                read_pct=d.read_pct,
                doc_type_found=d.doc_type_found,
                is_required=d.is_required,
            )
            novos_docs.append(novo_doc)
            db.add(novo_doc)
        db.commit()

        # copiar faturamento manual se existir
        original = db.query(Analysis).filter(Analysis.id == analysis_id_original).first()
        if original and original.faturamento_manual_json:
            novo = db.query(Analysis).filter(Analysis.id == novo_analysis_id).first()
            if novo:
                novo.faturamento_manual_json = original.faturamento_manual_json
                db.commit()

        # rodar análise usando o mesmo fluxo do análise original
        from services.analysis_service import run_full_analysis
        run_full_analysis(novo_analysis_id, novos_docs, db)

    except Exception as e:
        import traceback
        print(f"[REANALISE] ERRO {novo_analysis_id[:8]}: {e}", flush=True)
        traceback.print_exc()
        try:
            db.execute(
                sa_text("UPDATE analyses SET status='error' WHERE id=:aid"),
                {"aid": novo_analysis_id}
            )
            db.commit()
        except Exception:
            pass
    finally:
        db.close()

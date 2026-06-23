"""
Router — Analysis (trigger e status)
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from models.database import get_db, Analysis, Document
from services.analysis_service import run_full_analysis

router = APIRouter()


from fastapi import Body

@router.post("/{analysis_id}/run")
async def trigger_analysis(
    analysis_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    diretrizes: str = Body(default="", embed=True),
):
    """Dispara a análise completa em background."""
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")
    if analysis.status == "processing":
        raise HTTPException(409, "Análise já em processamento")

    documents = db.query(Document).filter(Document.analysis_id == analysis_id).all()

    # Rodar em background para não bloquear a resposta
    background_tasks.add_task(run_full_analysis, analysis_id, documents, db, diretrizes)

    return {"analysis_id": analysis_id, "status": "processing"}


@router.get("/{analysis_id}/status")
async def get_status(analysis_id: str, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")
    return {
        "status": analysis.status,
        "company_name": analysis.company_name,
        "cnpj": analysis.cnpj,
    }


from datetime import datetime


def _gerar_vibra_id_seq(db) -> str:
    """
    Gera o próximo ID Vibra no formato AAMM-NN.
    Sequencial baseado na contagem de análises com vibra_id no mês corrente.
    Exemplo: 2606-01, 2606-02, ..., 2606-99, 2606-100
    """
    now = datetime.utcnow()
    prefixo = now.strftime("%y%m")  # ex: 2606

    count = db.query(Analysis).filter(
        Analysis.vibra_id.like(prefixo + "-%")
    ).count()

    seq = count + 1
    return f"{prefixo}-{str(seq).zfill(2)}"


@router.post("/{analysis_id}/vibra-id")
async def criar_vibra_id(analysis_id: str, db: Session = Depends(get_db)):
    """
    Gera e persiste o ID Vibra para uma análise (chamado ao concluir análise).
    Se já existir, retorna o existente sem alterar.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    if not analysis.vibra_id:
        analysis.vibra_id = _gerar_vibra_id_seq(db)
        analysis.vibra_ver = 1
        db.commit()
        db.refresh(analysis)

    return {"vibra_id": analysis.vibra_id, "vibra_ver": analysis.vibra_ver or 1}


@router.post("/{analysis_id}/vibra-id/incrementar")
async def incrementar_vibra_ver(analysis_id: str, db: Session = Depends(get_db)):
    """
    Incrementa a versão do ID Vibra (chamado ao reabrir/reprocessar análise).
    Se não tiver ID ainda, gera um novo.
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    if not analysis.vibra_id:
        analysis.vibra_id = _gerar_vibra_id_seq(db)
        analysis.vibra_ver = 1
    else:
        analysis.vibra_ver = (analysis.vibra_ver or 1) + 1

    db.commit()
    db.refresh(analysis)
    return {"vibra_id": analysis.vibra_id, "vibra_ver": analysis.vibra_ver}


@router.get("/{analysis_id}/vibra-id")
async def get_vibra_id(analysis_id: str, db: Session = Depends(get_db)):
    """
    Retorna o ID Vibra atual de uma análise (usado ao exibir análise existente).
    """
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    return {
        "vibra_id": analysis.vibra_id,
        "vibra_ver": analysis.vibra_ver or 1
    }

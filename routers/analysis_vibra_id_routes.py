# ─────────────────────────────────────────────────────────────────
# RODADA I — Rotas de ID Vibra
# Adicionar ao arquivo backend/routers/analysis.py
#
# Dependências já existentes no arquivo: router, get_db, Session,
# Analysis (import de models.database), HTTPException, Depends
# ─────────────────────────────────────────────────────────────────

from datetime import datetime


def _gerar_vibra_id_seq(db) -> str:
    """
    Gera o próximo ID Vibra no formato AAMM-NN.
    Sequencial baseado na contagem de análises com vibra_id no mês corrente.
    Exemplo: 2606-01, 2606-02, ..., 2606-99, 2606-100
    """
    now = datetime.utcnow()
    prefixo = now.strftime("%y%m")  # ex: 2606

    # Contar quantas análises já têm vibra_id com este prefixo
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

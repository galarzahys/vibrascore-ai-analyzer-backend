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


# ── LISTA DE ANÁLISES (filtra por tenant) ──────────────────────

@router.get("/list")
async def listar_analyses(
    caller_email: str = "",
    db: Session = Depends(get_db),
):
    """
    Retorna lista resumida de análises para o menu.
    - superadmin: ve todas
    - otros: solo las de su client_id
    """
    from models.database import Report, Usuario

    # Determinar filtro por tenant
    client_id_filter = None
    if caller_email:
        caller = db.query(Usuario).filter(Usuario.email == caller_email).first()
        if caller and caller.perfil != "superadmin":
            client_id_filter = caller.client_id

    q = db.query(Analysis)
    if client_id_filter is not None:
        q = q.filter(Analysis.client_id == client_id_filter)

    analyses = q.order_by(Analysis.created_at.desc()).all()

    result = []
    for a in analyses:
        report = db.query(Report).filter(Report.analysis_id == a.id).first()
        result.append({
            "id": a.id,
            "empresa": a.company_name or "—",
            "cnpj": a.cnpj or "—",
            "analista": a.analyst_name or "—",
            "vibraId": a.vibra_id or "—",
            "status": a.status,
            "score": report.score_vibra if report else 0,
            "limite": report.limite_recomendado if report else 0,
            "data": a.created_at.strftime("%d/%m/%Y") if a.created_at else "—",
            "company_name": a.company_name,
        })
    return result


# ── PARECER DO ANALISTA ────────────────────────────────────────

import json as _json
from fastapi import Body as _Body


def _load_json_col(val):
    if not val:
        return {}
    try:
        return _json.loads(val)
    except Exception:
        return {}


def _get_report_or_404(analysis_id, db):
    from models.database import Report
    report = db.query(Report).filter(Report.analysis_id == analysis_id).first()
    if not report:
        raise HTTPException(404, "Relatório ainda não gerado para esta análise")
    return report


@router.get("/{analysis_id}/parecer")
async def get_parecer_analista(analysis_id: str, db: Session = Depends(get_db)):
    """Retorna array de pareceres de todos los analistas."""
    report = _get_report_or_404(analysis_id, db)
    data = _load_json_col(report.parecer_analista_json)
    # garantir que siempre retorna array
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and data:
        return [data]
    return []


@router.post("/{analysis_id}/parecer")
async def salvar_parecer_analista(
    analysis_id: str,
    body: dict = _Body(...),
    db: Session = Depends(get_db),
):
    """
    Guarda o atualiza el parecer del usuario actual.
    body debe incluir: user_id, email, nome, cargo, parecer, limite, etc.
    Si el usuario ya tiene parecer, sobrescribe el suyo. No toca los de otros.
    """
    from datetime import datetime
    report = _get_report_or_404(analysis_id, db)
 
    atual = _load_json_col(report.parecer_analista_json)
    if isinstance(atual, dict):
        atual = [atual] if atual else []
    elif not isinstance(atual, list):
        atual = []
 
    user_id = body.get("user_id") or body.get("email") or "desconhecido"
    # remover parecer anterior del mismo usuario
    atual = [p for p in atual if p.get("user_id") != user_id and p.get("email") != user_id]
 
    # agregar el nuevo
    novo = {
        "user_id": user_id,
        "email": body.get("email") or user_id,
        "nome": body.get("nome") or "",
        "cargo": body.get("cargo") or "",
        "parecer": body.get("parecer") or "",
        "limite": body.get("limite") or "",
        "limite_mem": body.get("limite_mem") or "",
        "notas_finais": body.get("notas_finais") or "",
        "pontos_fortes": body.get("pontos_fortes") or [],
        "pontos_atencao": body.get("pontos_atencao") or [],
        "condicionantes": body.get("condicionantes") or [],
        "ts": int(datetime.utcnow().timestamp() * 1000),
    }
    atual.append(novo)
 
    report.parecer_analista_json = _json.dumps(atual, ensure_ascii=False)
    db.commit()
    return atual


@router.get("/{analysis_id}/comite")
async def get_comite(analysis_id: str, db: Session = Depends(get_db)):
    report = _get_report_or_404(analysis_id, db)
    return _load_json_col(report.comite_json)


@router.post("/{analysis_id}/comite")
async def salvar_comite(
    analysis_id: str,
    body: dict = _Body(...),
    db: Session = Depends(get_db),
):
    report = _get_report_or_404(analysis_id, db)
    atual = _load_json_col(report.comite_json)
    atual.update(body)
    report.comite_json = _json.dumps(atual, ensure_ascii=False)
    db.commit()
    return atual


@router.get("/{analysis_id}/obs")
async def get_obs(analysis_id: str, db: Session = Depends(get_db)):
    report = _get_report_or_404(analysis_id, db)
    return _load_json_col(report.obs_json)


@router.patch("/{analysis_id}/obs")
async def salvar_obs(
    analysis_id: str,
    db: Session = Depends(get_db),
    aba_id: str = _Body(..., embed=True),
    texto: str = _Body(..., embed=True),
):
    report = _get_report_or_404(analysis_id, db)
    obs = _load_json_col(report.obs_json)
    obs[aba_id] = texto
    report.obs_json = _json.dumps(obs, ensure_ascii=False)
    db.commit()
    return obs


@router.get("/{analysis_id}/feedback")
async def get_feedback(analysis_id: str, db: Session = Depends(get_db)):
    report = _get_report_or_404(analysis_id, db)
    return _load_json_col(report.feedback_json)


@router.post("/{analysis_id}/feedback")
async def salvar_feedback(
    analysis_id: str,
    body: dict = _Body(...),
    db: Session = Depends(get_db),
):
    from datetime import datetime
    report = _get_report_or_404(analysis_id, db)
    atual = _load_json_col(report.feedback_json)
    atual.update(body)
    atual["ts"] = int(datetime.utcnow().timestamp() * 1000)
    report.feedback_json = _json.dumps(atual, ensure_ascii=False)
    db.commit()
    return atual

@router.patch("/{analysis_id}/status")
async def atualizar_status(
    analysis_id: str,
    db: Session = Depends(get_db),
    status: str = Body(..., embed=True),
):
    VALIDOS = {"done", "aguardando_comite", "em_deliberacao", "aprovado",
               "aprovado_com_ressalvas", "recusado"}
    if status not in VALIDOS:
        raise HTTPException(400, f"Status inválido")
    a = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not a:
        raise HTTPException(404, "Análise não encontrada")
    a.status = status
    db.commit()
    return {"status": a.status}


@router.get("/{analysis_id}/status")
async def get_status_atual(analysis_id: str, db: Session = Depends(get_db)):
    a = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not a:
        raise HTTPException(404, "Análise não encontrada")
    return {"status": a.status}
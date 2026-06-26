"""
Router — Reports (busca do relatório completo)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models.database import get_db, Analysis, Report, Document
import json

router = APIRouter()


@router.get("/{analysis_id}")
async def get_report(analysis_id: str, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")
    if analysis.status not in ("done", "aguardando_comite", "em_deliberacao", 
                                "aprovado", "aprovado_com_ressalvas", "recusado"):
        raise HTTPException(404, "Relatório ainda não disponível")

    report = db.query(Report).filter(Report.analysis_id == analysis_id).first()
    if not report:
        raise HTTPException(404, "Relatório não encontrado")

    documents = db.query(Document).filter(Document.analysis_id == analysis_id).all()

    def safe_json(val):
        if not val:
            return []
        try:
            return json.loads(val)
        except Exception:
            return []

    def safe_json_obj(val):
        if not val:
            return {}
        try:
            return json.loads(val)
        except Exception:
            return {}

    # Reconstruir estrutura completa a partir do raw_json se disponível
    raw = safe_json_obj(report.raw_json)

    return {
        "analysis_id": analysis_id,
        "status": analysis.status,
        "empresa": {
            "nome": report.empresa_nome,
            "cnpj": report.empresa_cnpj,
            "nome_fantasia": report.empresa_fantasia,
            "fundacao": report.empresa_fundacao,
            "regime_tributario": report.empresa_regime,
            "capital_social": report.empresa_capital,
            **(raw.get("empresa", {})),
        },
        "scores": {
            "bureau": report.score_bureau,
            "vibra_composto": report.score_vibra,
            "comportamental": report.score_comportamental,
            "financeiro": report.score_financeiro,
            "cadastral": report.score_cadastral,
            "tributario": report.score_tributario,
            "garantias": report.score_garantias,
            "cobertura_documental": report.score_cobertura,
        },
        "indicadores": {
            "liquidez_corrente": report.liquidez_corrente,
            "liquidez_seca": report.liquidez_seca,
            "endividamento_pl": report.endiv_pl,
            "margem_liquida": report.margem_liquida,
            "pmr_dias": report.pmr_dias,
            "pmp_dias": report.pmp_dias,
            "ciclo_financeiro": report.ciclo_financeiro,
            "ncg": report.ncg,
            "endiv_fat_meses": report.endiv_fat,
            **(raw.get("indicadores", {})),
        },
        "limite": {
            "recomendado": report.limite_recomendado,
            "memoria_calculo": report.limite_calc_memo,
            **(raw.get("limite", {})),
        },
        "parecer": report.parecer,
        "pontos_fortes": safe_json(report.pontos_fortes),
        "pontos_atencao": safe_json(report.pontos_atencao),
        "condicionantes": safe_json(report.condicionantes),
        "qsa": safe_json(report.qsa_analise),
        "grupo_economico": safe_json(report.grupo_economico),
        "raw": raw,
        "documentos": [
            {
                "field_key": d.field_key,
                "field_label": d.field_label,
                "original_name": d.original_name,
                "is_valid": d.is_valid,
                "is_required": d.is_required,
            }
            for d in documents
        ],
    }

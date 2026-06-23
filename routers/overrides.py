"""
Vibra Score — Router de Overrides do Analista (Etapa 2)

Sistema de edição inline de campos do relatório. Os overrides ficam
armazenados na coluna `reports.overrides_json` como JSON estruturado:

  {
    "<path>": {
      "valor": <qualquer valor JSON>,
      "por":   "<email/nome do usuário>",
      "em":    "<ISO timestamp UTC>"
    }
  }

Path no formato dotted: "empresa.regime", "inds.liquidez_corrente",
"faturamento_mensal.2024-03.valor", "pontos_fortes.2.titulo", etc.

ENDPOINTS:
  GET   /api/overrides/{analysis_id}            — retorna overrides_json
  PATCH /api/overrides/{analysis_id}            — batch update
  DELETE /api/overrides/{analysis_id}/{path}    — remove um override

NÃO altera o raw_json do motor singular (preserva o histórico da IA).
Frontend é responsável por aplicar overrides em cima do raw na renderização.
"""

import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session

from models.database import get_db, Report, Analysis

router = APIRouter()


def _load_overrides(report: Report) -> dict:
    """Lê overrides do report como dict (defaults para {} se vazio/inválido)."""
    if not report.overrides_json:
        return {}
    try:
        d = json.loads(report.overrides_json)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


@router.get("/{analysis_id}")
async def get_overrides(analysis_id: str, db: Session = Depends(get_db)):
    """Retorna o JSON de overrides do analista para esta análise."""
    report = db.query(Report).filter(Report.analysis_id == analysis_id).first()
    if not report:
        # análise pode existir mas ainda não ter Report — devolve vazio
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if not analysis:
            raise HTTPException(404, "Análise não encontrada")
        return {"analysis_id": analysis_id, "overrides": {}}

    return {
        "analysis_id": analysis_id,
        "overrides": _load_overrides(report),
    }


@router.patch("/{analysis_id}")
async def patch_overrides(
    analysis_id: str,
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """
    Atualiza overrides em batch.

    Body esperado:
      {
        "por": "usuario@cliente.com",      # quem está editando
        "changes": [
          {"path": "empresa.regime",       "valor": "Lucro Real"},
          {"path": "inds.liquidez_corrente", "valor": 1.45},
          {"path": "pontos_fortes.2",      "remover": true},          # apaga override
          ...
        ]
      }

    Retorna o objeto overrides atualizado.

    Bloqueio: caminhos não-editáveis (score_vibra, limite_recomendado, parecer,
    limite_calc_memo, score_classe) são rejeitados silenciosamente.
    """
    report = db.query(Report).filter(Report.analysis_id == analysis_id).first()
    if not report:
        raise HTTPException(404, "Report não encontrado para esta análise")

    overrides = _load_overrides(report)

    por = (body.get("por") or "?").strip()[:120]
    changes = body.get("changes") or []
    if not isinstance(changes, list):
        raise HTTPException(400, "Campo 'changes' deve ser uma lista")

    # Lista de prefixos de path proibidos (não-editáveis pela política de produto)
    PATHS_BLOQUEADOS = (
        "score_vibra",
        "score_classe",
        "limite_recomendado",
        "limite_calc_memo",
        "parecer",   # Parecer IA principal (campo Report.parecer)
    )

    agora = datetime.utcnow().isoformat()
    aplicados = 0
    bloqueados = 0

    for ch in changes:
        if not isinstance(ch, dict):
            continue
        path = ch.get("path")
        if not path or not isinstance(path, str):
            continue
        # Verifica bloqueio (prefixo exato ou path começando com prefixo + ".")
        if any(path == p or path.startswith(p + ".") for p in PATHS_BLOQUEADOS):
            bloqueados += 1
            continue

        if ch.get("remover"):
            overrides.pop(path, None)
            aplicados += 1
        else:
            overrides[path] = {
                "valor": ch.get("valor"),
                "por":   por,
                "em":    agora,
            }
            aplicados += 1

    report.overrides_json = json.dumps(overrides, ensure_ascii=False)
    db.commit()

    return {
        "ok": True,
        "analysis_id": analysis_id,
        "aplicados": aplicados,
        "bloqueados": bloqueados,
        "overrides": overrides,
    }


@router.delete("/{analysis_id}/{path:path}")
async def delete_override(
    analysis_id: str,
    path: str,
    db: Session = Depends(get_db),
):
    """Remove um override específico por path."""
    report = db.query(Report).filter(Report.analysis_id == analysis_id).first()
    if not report:
        raise HTTPException(404, "Report não encontrado")

    overrides = _load_overrides(report)
    existia = path in overrides
    overrides.pop(path, None)
    report.overrides_json = json.dumps(overrides, ensure_ascii=False)
    db.commit()

    return {"ok": True, "removido": existia, "path": path}

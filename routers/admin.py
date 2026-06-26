"""
Router — Admin config
Reemplaza localStorage: vs_plataforma_nome, vs_score_min, vs_admin_senha
"""

import json
from fastapi import APIRouter, Depends, Body, HTTPException
from sqlalchemy.orm import Session
from models.database import get_db, AdminConfig

router = APIRouter()

DEFAULTS = {
    "plataforma_nome": "VibraScore",
    "score_min": 0,
    "admin_senha": "vibra2024",
}


def _get_or_create(db: Session) -> AdminConfig:
    cfg = db.query(AdminConfig).filter(AdminConfig.id == 1).first()
    if not cfg:
        cfg = AdminConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@router.get("/config")
async def get_config(db: Session = Depends(get_db)):
    cfg = _get_or_create(db)
    return {
        "plataforma_nome": cfg.plataforma_nome or DEFAULTS["plataforma_nome"],
        "score_min": cfg.score_min or DEFAULTS["score_min"],
        "admin_senha": cfg.admin_senha or DEFAULTS["admin_senha"],
    }


@router.post("/config")
async def salvar_config(
    db: Session = Depends(get_db),
    plataforma_nome: str = Body(default=None, embed=True),
    score_min: int = Body(default=None, embed=True),
    admin_senha: str = Body(default=None, embed=True),
):
    cfg = _get_or_create(db)
    if plataforma_nome is not None:
        cfg.plataforma_nome = plataforma_nome
    if score_min is not None:
        cfg.score_min = score_min
    if admin_senha is not None and admin_senha.strip():
        cfg.admin_senha = admin_senha.strip()
    db.commit()
    return {
        "plataforma_nome": cfg.plataforma_nome,
        "score_min": cfg.score_min,
        "admin_senha": cfg.admin_senha,
    }


from models.database import DocChecklist  # agregar al import de database.py también


# ── DOC CHECKLIST ──────────────────────────────────────────────

@router.get("/doc-checklist")
async def get_doc_checklist(db: Session = Depends(get_db)):
    docs = db.query(DocChecklist).order_by(DocChecklist.ordem).all()
    return [{
        "id": d.id,
        "field_key": d.field_key,
        "label": d.label,
        "required": d.required,
        "formats": d.formats,
        "ativo": bool(d.ativo),
        "ordem": d.ordem,
    } for d in docs]


@router.post("/doc-checklist")
async def criar_doc(
    db: Session = Depends(get_db),
    field_key: str = Body(..., embed=True),
    label: str = Body(..., embed=True),
    required: str = Body(default="opcional", embed=True),
    formats: str = Body(default=".pdf", embed=True),
):
    field_key = field_key.strip().lower().replace(" ", "_")
    if db.query(DocChecklist).filter(DocChecklist.field_key == field_key).first():
        raise HTTPException(400, "field_key já existe")
    if required not in ("hard", "obrigatorio", "opcional"):
        raise HTTPException(400, "required deve ser: hard, obrigatorio ou opcional")
    ultimo = db.query(DocChecklist).order_by(DocChecklist.ordem.desc()).first()
    ordem = (ultimo.ordem + 1) if ultimo else 1
    d = DocChecklist(field_key=field_key, label=label.strip(),
                     required=required, formats=formats.strip(), ativo=True, ordem=ordem)
    db.add(d)
    db.commit()
    db.refresh(d)
    return {"id": d.id, "field_key": d.field_key, "label": d.label,
            "required": d.required, "formats": d.formats, "ativo": bool(d.ativo), "ordem": d.ordem}


@router.patch("/doc-checklist/{doc_id}")
async def editar_doc(
    doc_id: int,
    db: Session = Depends(get_db),
    label: str = Body(default=None, embed=True),
    required: str = Body(default=None, embed=True),
    formats: str = Body(default=None, embed=True),
    ativo: bool = Body(default=None, embed=True),
    ordem: int = Body(default=None, embed=True),
):
    d = db.query(DocChecklist).filter(DocChecklist.id == doc_id).first()
    if not d:
        raise HTTPException(404, "Documento não encontrado")
    if label is not None:
        d.label = label.strip()
    if required is not None:
        if required not in ("obrigatorio", "opcional"):
            raise HTTPException(400, "required deve ser: obrigatorio ou opcional")
        d.required = required
    if formats is not None:
        d.formats = formats.strip()
    if ativo is not None:
        d.ativo = ativo
    if ordem is not None:
        d.ordem = ordem
    db.commit()
    return {"ok": True}


@router.delete("/doc-checklist/{doc_id}")
async def deletar_doc(doc_id: int, db: Session = Depends(get_db)):
    d = db.query(DocChecklist).filter(DocChecklist.id == doc_id).first()
    if not d:
        raise HTTPException(404, "Documento não encontrado")
    db.delete(d)
    db.commit()
    return {"deleted": True}

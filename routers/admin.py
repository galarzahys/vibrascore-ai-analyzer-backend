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

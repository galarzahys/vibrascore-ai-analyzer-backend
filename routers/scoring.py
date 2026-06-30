"""
Vibra Score — Router de calibração do score (multi-tenant)
Endpoints:
  GET  /api/scoring/config?client_id=X  — retorna config do tenant (ou global se não existir)
  PUT  /api/scoring/config              — atualiza (admin do tenant ou superadmin)
  POST /api/scoring/config/reset        — restaura defaults

Regra de fallback:
  - client_id=None ou vazio  -> usa/edita a config GLOBAL (client_id=NULL no banco)
  - client_id=<uuid>         -> usa a config do tenant; se não existir, cria uma cópia
                                 dos valores da config global na primeira gravação (PUT)
                                 e para leitura (GET) retorna a global se a do tenant não existir.
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from models.database import SessionLocal, ScoringConfig

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

DEFAULTS = {
    "peso_bureau":         25.0,
    "peso_financeiro":     25.0,
    "peso_comportamental": 15.0,
    "peso_cadastral":      10.0,
    "peso_tributario":     10.0,
    "peso_garantias":      10.0,
    "peso_cobertura":       5.0,
    "limite_a": 900, "limite_b": 800, "limite_c": 700, "limite_d": 600,
    "limite_e": 500, "limite_f": 400, "limite_g": 300, "limite_h": 200,
    "limite_i": 100,
}

PESO_FIELDS = ["peso_bureau", "peso_financeiro", "peso_comportamental",
               "peso_cadastral", "peso_tributario", "peso_garantias", "peso_cobertura"]
LIMITE_FIELDS = ["limite_a","limite_b","limite_c","limite_d","limite_e",
                 "limite_f","limite_g","limite_h","limite_i"]


def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()


def get_global_config(db: Session) -> ScoringConfig:
    """Retorna (ou cria) a config global — client_id IS NULL."""
    cfg = db.query(ScoringConfig).filter(ScoringConfig.client_id.is_(None)).first()
    if not cfg:
        cfg = ScoringConfig(client_id=None, **DEFAULTS)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def get_tenant_config(db: Session, client_id: str) -> Optional[ScoringConfig]:
    """Retorna a config específica do tenant, ou None se não existir."""
    return db.query(ScoringConfig).filter(ScoringConfig.client_id == client_id).first()


def get_effective_config(db: Session, client_id: Optional[str]) -> ScoringConfig:
    """
    Config efetiva para LEITURA: tenant se existir, senão global (fallback).
    Usado tanto pelo endpoint GET quanto pelo motor de análise.
    """
    if client_id:
        cfg = get_tenant_config(db, client_id)
        if cfg:
            return cfg
    return get_global_config(db)


def _cfg_to_dict(cfg: ScoringConfig) -> dict:
    return {f: getattr(cfg, f) for f in PESO_FIELDS + LIMITE_FIELDS}


class ScoringConfigIn(BaseModel):
    peso_bureau:         float = Field(..., ge=0, le=100)
    peso_financeiro:     float = Field(..., ge=0, le=100)
    peso_comportamental: float = Field(..., ge=0, le=100)
    peso_cadastral:      float = Field(..., ge=0, le=100)
    peso_tributario:     float = Field(..., ge=0, le=100)
    peso_garantias:      float = Field(..., ge=0, le=100)
    peso_cobertura:      float = Field(..., ge=0, le=100)
    limite_a: int = Field(..., ge=0, le=1000)
    limite_b: int = Field(..., ge=0, le=1000)
    limite_c: int = Field(..., ge=0, le=1000)
    limite_d: int = Field(..., ge=0, le=1000)
    limite_e: int = Field(..., ge=0, le=1000)
    limite_f: int = Field(..., ge=0, le=1000)
    limite_g: int = Field(..., ge=0, le=1000)
    limite_h: int = Field(..., ge=0, le=1000)
    limite_i: int = Field(..., ge=0, le=1000)
    updated_by: Optional[str] = None
    client_id: Optional[str] = None  # tenant alvo (None = global)


def _validate_payload(p: ScoringConfigIn):
    soma = sum(getattr(p, f) for f in PESO_FIELDS)
    if abs(soma - 100.0) > 0.1:
        raise HTTPException(400, f"Soma dos pesos deve ser 100%. Atual: {soma:.2f}%")
    limites = [getattr(p, f) for f in LIMITE_FIELDS]
    for i in range(len(limites) - 1):
        if limites[i] <= limites[i+1]:
            raise HTTPException(400, f"Limites devem ser estritamente decrescentes (A > B > ... > I). "
                                     f"Erro entre {LIMITE_FIELDS[i]}={limites[i]} e {LIMITE_FIELDS[i+1]}={limites[i+1]}.")


# ===== Endpoints =====

@router.get("/config")
def get_config(
    db: Session = Depends(get_db),
    client_id: Optional[str] = Query(default=None),
):
    """
    Retorna a config efetiva: do tenant se existir e for informado client_id,
    senão a global. is_custom indica se o tenant tem config própria.
    """
    cfg = get_effective_config(db, client_id)
    is_custom = bool(client_id) and cfg.client_id == client_id
    return {
        **_cfg_to_dict(cfg),
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        "updated_by": cfg.updated_by,
        "defaults": DEFAULTS,
        "is_custom": is_custom,
        "client_id": client_id,
    }


@router.put("/config")
def update_config(payload: ScoringConfigIn, db: Session = Depends(get_db),
                  x_user_role: Optional[str] = Header(None)):
    if x_user_role and x_user_role.lower() not in ("admin", "administrador", "superadmin"):
        raise HTTPException(403, "Apenas administradores podem alterar a calibração.")
    _validate_payload(payload)

    target_client_id = payload.client_id

    if target_client_id:
        cfg = get_tenant_config(db, target_client_id)
        if not cfg:
            cfg = ScoringConfig(client_id=target_client_id)
            db.add(cfg)
    else:
        cfg = get_global_config(db)

    for f in PESO_FIELDS + LIMITE_FIELDS:
        setattr(cfg, f, getattr(payload, f))
    cfg.updated_by = payload.updated_by or x_user_role or "admin"
    db.commit()
    db.refresh(cfg)
    return {"ok": True, **_cfg_to_dict(cfg),
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
            "updated_by": cfg.updated_by,
            "client_id": cfg.client_id}


@router.post("/config/reset")
def reset_config(
    db: Session = Depends(get_db),
    x_user_role: Optional[str] = Header(None),
    client_id: Optional[str] = Query(default=None),
):
    if x_user_role and x_user_role.lower() not in ("admin", "administrador", "superadmin"):
        raise HTTPException(403, "Apenas administradores podem restaurar a calibração.")

    if client_id:
        cfg = get_tenant_config(db, client_id)
        if not cfg:
            cfg = ScoringConfig(client_id=client_id)
            db.add(cfg)
    else:
        cfg = get_global_config(db)

    for k, v in DEFAULTS.items():
        setattr(cfg, k, v)
    cfg.updated_by = x_user_role or "admin"
    db.commit()
    db.refresh(cfg)
    return {"ok": True, **_cfg_to_dict(cfg), "client_id": cfg.client_id}

"""
Vibra Score — Router de calibração do score
Endpoints:
  GET  /api/scoring/config         — retorna pesos + limites + defaults
  PUT  /api/scoring/config         — atualiza (admin apenas; valida soma=100)
  POST /api/scoring/config/reset   — restaura defaults (admin apenas)

Etapa 5 — Calibração configurável.
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Body
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from models.database import SessionLocal, ScoringConfig, AdminConfig

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

# Defaults (mesmos do banco, mas em memória para o endpoint de reset)
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


def get_or_create_config(db: Session) -> ScoringConfig:
    cfg = db.query(ScoringConfig).filter(ScoringConfig.id == 1).first()
    if not cfg:
        cfg = ScoringConfig(id=1, **DEFAULTS)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _cfg_to_dict(cfg: ScoringConfig) -> dict:
    return {f: getattr(cfg, f) for f in PESO_FIELDS + LIMITE_FIELDS}


# ===== Schemas =====
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


# ===== Endpoints =====
@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    cfg = get_or_create_config(db)
    return {
        **_cfg_to_dict(cfg),
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        "updated_by": cfg.updated_by,
        "defaults": DEFAULTS,
    }


def _validate_payload(p: ScoringConfigIn):
    # Soma dos pesos deve ser 100% (tolerância 0.1)
    soma = sum(getattr(p, f) for f in PESO_FIELDS)
    if abs(soma - 100.0) > 0.1:
        raise HTTPException(400, f"Soma dos pesos deve ser 100%. Atual: {soma:.2f}%")
    # Limites devem ser estritamente decrescentes (A > B > ... > I)
    limites = [getattr(p, f) for f in LIMITE_FIELDS]
    for i in range(len(limites) - 1):
        if limites[i] <= limites[i+1]:
            raise HTTPException(400, f"Limites devem ser estritamente decrescentes (A > B > ... > I). "
                                     f"Erro entre {LIMITE_FIELDS[i]}={limites[i]} e {LIMITE_FIELDS[i+1]}={limites[i+1]}.")


@router.put("/config")
def update_config(payload: ScoringConfigIn, db: Session = Depends(get_db),
                  x_user_role: Optional[str] = Header(None)):
    # Restrição de papel: só admin pode alterar
    if x_user_role and x_user_role.lower() not in ("admin", "administrador"):
        raise HTTPException(403, "Apenas administradores podem alterar a calibração.")
    _validate_payload(payload)
    cfg = get_or_create_config(db)
    for f in PESO_FIELDS + LIMITE_FIELDS:
        setattr(cfg, f, getattr(payload, f))
    cfg.updated_by = payload.updated_by or x_user_role or "admin"
    db.commit()
    db.refresh(cfg)
    return {"ok": True, **_cfg_to_dict(cfg),
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
            "updated_by": cfg.updated_by}


@router.post("/config/reset")
def reset_config(db: Session = Depends(get_db), x_user_role: Optional[str] = Header(None)):
    if x_user_role and x_user_role.lower() not in ("admin", "administrador"):
        raise HTTPException(403, "Apenas administradores podem restaurar a calibração.")
    cfg = get_or_create_config(db)
    for k, v in DEFAULTS.items():
        setattr(cfg, k, v)
    cfg.updated_by = x_user_role or "admin"
    db.commit()
    db.refresh(cfg)
    return {"ok": True, **_cfg_to_dict(cfg)}


    from models.database import AdminConfig 


@router.get("/defasagem")
def get_defasagem(db: Session = Depends(get_db)):
    cfg = db.query(AdminConfig).filter(AdminConfig.id == 1).first()
    if not cfg or not cfg.defasagem_json:
        # defaults
        return {
            "bureau": 30, "scr": 90, "faturamento": 90,
            "balanco": 365, "dre": 365, "endividamento": 90,
            "irpf": 365, "contrato": 730, "certidoes": 90,
        }
    try:
        return json.loads(cfg.defasagem_json)
    except Exception:
        return {}


@router.post("/defasagem")
def salvar_defasagem(body: dict = Body(...), db: Session = Depends(get_db)):
    cfg = db.query(AdminConfig).filter(AdminConfig.id == 1).first()
    if not cfg:
        cfg = AdminConfig(id=1)
        db.add(cfg)
    cfg.defasagem_json = json.dumps(body, ensure_ascii=False)
    db.commit()
    return body
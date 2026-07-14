"""
Router — Integrations (consulta API externa Vibra Full / SCR)
Suporta client_id NULL para equipe interna (path param: "interno")
"""
import json
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.database import get_db, Analysis, Document

router = APIRouter()

# URL base — configurável via .env
VIBRA_API_BASE = "https://api.vibrascore.com.br"
#VIBRA_API_BASE = "http://localhost:7116"

TIPO_CONSULTA = {
    "bureau": 4,   # Vibra Full CNPJ
    "scr": 2,     # Expert SCR
}


def _resolve_client_id(client_id: str):
    """'interno' representa a equipe interna (client_id NULL no banco)."""
    return None if client_id == "interno" else client_id


def _get_credenciais(db: Session, client_id) -> dict:
    """Busca credenciais pelo client_id (pode ser None para equipe interna)."""
    if client_id is None:
        row = db.execute(
            text("SELECT api_key, api_secret, sandbox FROM api_integrations WHERE client_id IS NULL AND ativo = 1")
        ).fetchone()
    else:
        row = db.execute(
            text("SELECT api_key, api_secret, sandbox FROM api_integrations WHERE client_id = :cid AND ativo = 1"),
            {"cid": client_id}
        ).fetchone()

    if not row or not row[0] or not row[1]:
        raise HTTPException(400, "Credenciais de integração não configuradas. Configure em Configurações > Integração.")
    return {"api_key": row[0], "api_secret": row[1], "sandbox": bool(row[2])}


def _build_url(sandbox: bool, path: str) -> str:
    base_path = "/v1/integration/sandbox/queries" if sandbox else "/v1/integration/queries"
    return f"{VIBRA_API_BASE}{base_path}{path}"


# ── INICIAR CONSULTA ────────────────────────────────────────────

@router.post("/{analysis_id}/iniciar")
async def iniciar_consulta(
    analysis_id: str,
    db: Session = Depends(get_db),
    field_key: str = Body(..., embed=True),
    cnpj: str = Body(..., embed=True),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    if field_key not in TIPO_CONSULTA:
        raise HTTPException(400, f"field_key inválido. Use: {list(TIPO_CONSULTA.keys())}")

    # client_id pode ser None para equipe interna
    creds = _get_credenciais(db, analysis.client_id)

    tipo = TIPO_CONSULTA[field_key]
    url = _build_url(creds["sandbox"], "")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                url,
                headers={
                    "Api-Key": creds["api_key"],
                    "Api-Secret": creds["api_secret"],
                    "Content-Type": "application/json",
                },
                json={"identificador": cnpj, "tipo_consulta": tipo},
            )
    except httpx.RequestError as e:
        raise HTTPException(503, f"Erro de conexão com API externa: {str(e)}")

    if r.status_code == 401:
        raise HTTPException(401, "Credenciais inválidas ou expiradas.")
    if r.status_code == 404:
        raise HTTPException(404, "CNPJ não encontrado nas fontes de dados.")
    if r.status_code not in (200, 201):
        try:
            detail = r.json().get("message", r.text[:200])
        except Exception:
            detail = r.text[:200]
        raise HTTPException(r.status_code, f"Erro na API externa: {detail}")

    data = r.json()
    request_id = str(data.get("id_Consulta") or data.get("id") or "")
    if not request_id:
        raise HTTPException(500, "API externa não retornou id_Consulta")

    if field_key == "bureau":
        analysis.integration_bureau_id = request_id
    else:
        analysis.integration_scr_id = request_id
    db.commit()

    return {
        "request_id": request_id,
        "status": data.get("status", "Processamento"),
        "mensagem": data.get("mensagem", "Consulta em processamento."),
        "sandbox": creds["sandbox"],
    }


# ── VERIFICAR STATUS / OBTER RESULTADO ─────────────────────────

@router.get("/{analysis_id}/status/{request_id}")
async def verificar_status(
    analysis_id: str,
    request_id: str,
    field_key: str = Query(...),
    db: Session = Depends(get_db),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")

    creds = _get_credenciais(db, analysis.client_id)
    url = _build_url(creds["sandbox"], f"/{request_id}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                url,
                headers={
                    "Api-Key": creds["api_key"],
                    "Api-Secret": creds["api_secret"],
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(503, f"Erro de conexão: {str(e)}")

    if r.status_code != 200:
        raise HTTPException(r.status_code, "Erro ao verificar status da consulta")

    data = r.json()
    status = (data.get("consulta", {}).get("status") or data.get("status") or "").lower()

    # detectar conclusão por presença dos dados ou status conhecido
    tem_dados = bool(data.get("bureau_1") or data.get("scr") or data.get("situacao_cadastral"))
    concluido = tem_dados or status in ("concluido", "concluída", "concluída", "completed", "done", "finalizado", "-", "")

    if not concluido:
        return {"concluido": False, "status": status}

    # consulta concluída — salvar JSON e criar Document
    texto = json.dumps(data, ensure_ascii=False, indent=2)

    field_labels = {
        "bureau": "Bureau de Crédito (Vibra Full) — via API",
        "scr": "SCR / BACEN — via API",
    }

    doc_existente = db.query(Document).filter(
        Document.analysis_id == analysis_id,
        Document.field_key == field_key,
        Document.s3_key.like("integration:%"),
    ).first()

    if doc_existente:
        doc_existente.is_valid = True
        doc_existente.validation_msg = "Obtido via API de integração"
        db.commit()
        doc = doc_existente
    else:
        upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads", analysis_id)
        os.makedirs(upload_dir, exist_ok=True)
        json_filename = f"{field_key}_api_{request_id}.json"
        json_path = os.path.join(upload_dir, json_filename)
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(texto)

        doc = Document(
            analysis_id=analysis_id,
            field_key=field_key,
            field_label=field_labels.get(field_key, field_key),
            original_name=json_filename,
            s3_key=f"local:{json_filename}",
            file_size=len(texto.encode()),
            mime_type="application/json",
            is_valid=True,
            validation_msg="Obtido via API de integração",
            read_pct=100.0,
            doc_type_found=field_labels.get(field_key, field_key),
            is_required=True,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    return {
        "concluido": True,
        "status": status,
        "document_id": doc.id,
        "field_key": field_key,
    }


# ── CRUD DE CREDENCIAIS ─────────────────────────────────────────

@router.get("/credenciais/{client_id}")
async def get_credenciais(client_id: str, db: Session = Depends(get_db)):
    resolved = _resolve_client_id(client_id)
    if resolved is None:
        row = db.execute(
            text("SELECT api_key, api_secret, sandbox, ativo FROM api_integrations WHERE client_id IS NULL")
        ).fetchone()
    else:
        row = db.execute(
            text("SELECT api_key, api_secret, sandbox, ativo FROM api_integrations WHERE client_id = :cid"),
            {"cid": resolved}
        ).fetchone()

    if not row:
        return {"configurado": False, "sandbox": True, "ativo": False, "api_key": ""}
    return {
        "configurado": bool(row[0]),
        "api_key": row[0] or "",
        "api_secret": "••••••••" if row[1] else "",
        "sandbox": bool(row[2]),
        "ativo": bool(row[3]),
    }


@router.post("/credenciais/{client_id}")
async def salvar_credenciais(
    client_id: str,
    db: Session = Depends(get_db),
    api_key: str = Body(..., embed=True),
    api_secret: str = Body(default=None, embed=True),
    sandbox: bool = Body(default=False, embed=True),
):
    resolved = _resolve_client_id(client_id)

    # verificar se já existe
    if resolved is None:
        existing = db.execute(
            text("SELECT id, api_secret FROM api_integrations WHERE client_id IS NULL")
        ).fetchone()
    else:
        existing = db.execute(
            text("SELECT id, api_secret FROM api_integrations WHERE client_id = :cid"),
            {"cid": resolved}
        ).fetchone()

    # se não passou api_secret novo, manter o existente
    secret_to_save = api_secret if api_secret else (existing[1] if existing else None)
    if not secret_to_save:
        raise HTTPException(400, "API Secret é obrigatório na primeira configuração.")

    if existing:
        if resolved is None:
            db.execute(
                text("UPDATE api_integrations SET api_key=:k, api_secret=:s, sandbox=:sb, ativo=1, updated_at=datetime('now') WHERE client_id IS NULL"),
                {"k": api_key, "s": secret_to_save, "sb": int(sandbox)}
            )
        else:
            db.execute(
                text("UPDATE api_integrations SET api_key=:k, api_secret=:s, sandbox=:sb, ativo=1, updated_at=datetime('now') WHERE client_id=:cid"),
                {"k": api_key, "s": secret_to_save, "sb": int(sandbox), "cid": resolved}
            )
    else:
        db.execute(
            text("INSERT INTO api_integrations (client_id, api_key, api_secret, sandbox, ativo) VALUES (:cid, :k, :s, :sb, 1)"),
            {"cid": resolved, "k": api_key, "s": secret_to_save, "sb": int(sandbox)}
        )
    db.commit()
    return {"ok": True, "sandbox": sandbox}
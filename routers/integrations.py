"""
Router — Integrations (consulta API externa Vibra Full / SCR)
"""
import json
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from models.database import get_db, Analysis, Document
import uuid

router = APIRouter()

# URL base — configurável via .env
VIBRA_API_BASE = "https://api.vibrascore.com.br"
#VIBRA_API_BASE = "http://localhost:7116"

TIPO_CONSULTA = {
    "bureau": 4,   # Vibra Full CNPJ
    "scr": 2,     # Expert SCR
}


def _get_credenciais(db: Session, client_id: str) -> dict:
    from sqlalchemy import text
    row = db.execute(
        text("SELECT api_key, api_secret, sandbox FROM api_integrations WHERE client_id = :cid AND ativo = 1"),
        {"cid": client_id}
    ).fetchone()
    if not row or not row[0] or not row[1]:
        raise HTTPException(400, "Credenciais de integração não configuradas para esta empresa. Configure em Configurações > Integração.")
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

    # buscar credenciais do tenant
    if not analysis.client_id:
        raise HTTPException(400, "Análise sem empresa associada. Não é possível usar integração.")

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
        raise HTTPException(401, "Credenciais inválidas ou expiradas. Verifique as configurações de integração.")
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

    # salvar o id na análise para controle
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
    field_key: str,
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

    # status que indicam conclusão
    # detectar conclusión por presença dos dados, não apenas pelo status
    tem_dados = bool(data.get("bureau_1") or data.get("scr") or data.get("situacao_cadastral"))
    concluido = tem_dados or status in ("concluido", "concluída", "completed", "done", "finalizado", "-", "")

    if not concluido:
        return {"concluido": False, "status": status}

    # consulta concluída — converter JSON para texto e criar Document
    texto = json.dumps(data, ensure_ascii=False, indent=2)

    # verificar se já existe documento desta integração para este campo
    doc_existente = db.query(Document).filter(
        Document.analysis_id == analysis_id,
        Document.field_key == field_key,
        Document.s3_key.like("integration:%"),
    ).first()

    field_labels = {
        "bureau": "Bureau de Crédito (Vibra Full) — via API",
        "scr": "SCR / BACEN — via API",
    }

    if doc_existente:
        doc_existente.validation_msg = "Obtido via API de integração"
        doc_existente.is_valid = True
        db.commit()
        doc = doc_existente
    else:
        doc = Document(
            analysis_id=analysis_id,
            field_key=field_key,
            field_label=field_labels.get(field_key, field_key),
            original_name=f"{field_key}_api_{request_id}.json",
            s3_key=f"integration:{request_id}:{field_key}",
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

        # salvar o JSON em arquivo local para o motor de análise poder ler
        upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads", analysis_id)
        os.makedirs(upload_dir, exist_ok=True)
        json_path = os.path.join(upload_dir, f"{field_key}_api_{request_id}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(texto)
        # atualizar s3_key para local
        doc.s3_key = f"local:{field_key}_api_{request_id}.json"
        db.commit()

    return {
        "concluido": True,
        "status": status,
        "document_id": doc.id,
        "field_key": field_key,
    }


# ── CRUD DE CREDENCIAIS ─────────────────────────────────────────

@router.get("/credenciais/{client_id}")
async def get_credenciais(client_id: str, db: Session = Depends(get_db)):
    from sqlalchemy import text
    row = db.execute(
        text("SELECT api_key, api_secret, sandbox, ativo FROM api_integrations WHERE client_id = :cid"),
        {"cid": client_id}
    ).fetchone()
    if not row:
        return {"configurado": False, "sandbox": True, "ativo": False}
    return {
        "configurado": bool(row[0]),
        "api_key": row[0] or "",
        "api_secret": "••••••••" if row[1] else "",  # nunca expor o secret
        "sandbox": bool(row[2]),
        "ativo": bool(row[3]),
    }


@router.post("/credenciais/{client_id}")
async def salvar_credenciais(
    client_id: str,
    db: Session = Depends(get_db),
    api_key: str = Body(..., embed=True),
    api_secret: str = Body(..., embed=True),
    sandbox: bool = Body(default=False, embed=True),
):
    from sqlalchemy import text
    existing = db.execute(
        text("SELECT id FROM api_integrations WHERE client_id = :cid"),
        {"cid": client_id}
    ).fetchone()

    if existing:
        db.execute(
            text("UPDATE api_integrations SET api_key=:k, api_secret=:s, sandbox=:sb, ativo=1, updated_at=datetime('now') WHERE client_id=:cid"),
            {"k": api_key, "s": api_secret, "sb": int(sandbox), "cid": client_id}
        )
    else:
        db.execute(
            text("INSERT INTO api_integrations (client_id, api_key, api_secret, sandbox, ativo) VALUES (:cid, :k, :s, :sb, 1)"),
            {"cid": client_id, "k": api_key, "s": api_secret, "sb": int(sandbox)}
        )
    db.commit()
    return {"ok": True, "sandbox": sandbox}

"""
Router — Grupos Econômicos
Rodada P (Backend) — Análise consolidada de grupo econômico.

Princípios:
- Análises filhas continuam sendo Analysis normais, com grupo_id preenchido.
- Motor de análise singular (analysis_service.py) NÃO é alterado.
- Documentos consolidados moram em GrupoDocument (tabela separada).
- Sem duplicação: doc consolidado é referenciado por todas as filhas na hora da análise.
- Hard-cap de 4 empresas por grupo (decisão 8).
- ID Vibra do grupo: G + AAMM-NN, sequencial independente do singular (decisão 4).
"""

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Body, BackgroundTasks
from fastapi.responses import FileResponse as FR
from sqlalchemy.orm import Session
from sqlalchemy import asc
from models.database import get_db, Grupo, GrupoDocument, Analysis, Document, SessionLocal
from services.document_validator import validate_document_with_ai
from services.analysis_service import run_full_analysis
from datetime import datetime
import os
import shutil
import traceback
import time
import random
import json
from services.group_consolidator import run_consolidated_analysis

router = APIRouter()

MAX_EMPRESAS_POR_GRUPO = 4   # decisão 8
MAX_FILES_PER_FIELD    = 5   # mesmo padrão da tabela documents

UPLOAD_DIR_GRUPOS = os.path.join(os.path.dirname(__file__), "..", "uploads_grupos")


def _safe_name_grupo(doc: GrupoDocument) -> str:
    """Extrai o nome seguro do arquivo a partir do s3_key local."""
    if doc.s3_key and doc.s3_key.startswith("local:"):
        return doc.s3_key[6:]
    return f"{doc.field_key}_{doc.original_name}"


# ── CRUD DO GRUPO ─────────────────────────────────────────────────

@router.post("/create")
async def criar_grupo(
    db: Session = Depends(get_db),
    nome: str = Body(..., embed=True),
    client_id: str = Body(default=None, embed=True),
    analista: str = Body(default=None, embed=True),
):
    """Cria um novo grupo econômico (sem empresas filhas ainda)."""
    if not nome or not nome.strip():
        raise HTTPException(400, "Nome do grupo é obrigatório")

    grupo = Grupo(
        nome=nome.strip(),
        client_id=client_id,
        analista=analista,
    )
    db.add(grupo)
    db.commit()
    db.refresh(grupo)

    return {"grupo_id": grupo.id, "nome": grupo.nome}


@router.get("/{grupo_id}")
async def obter_grupo(grupo_id: str, db: Session = Depends(get_db)):
    """Retorna grupo + empresas filhas + docs consolidados."""
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    empresas = (
        db.query(Analysis)
        .filter(Analysis.grupo_id == grupo_id)
        .order_by(asc(Analysis.ordem_no_grupo))
        .all()
    )

    docs = db.query(GrupoDocument).filter(GrupoDocument.grupo_id == grupo_id).all()

    return {
        "id": grupo.id,
        "nome": grupo.nome,
        "client_id": grupo.client_id,
        "analista": grupo.analista,
        "diretrizes": grupo.diretrizes,
        "vibra_id": grupo.vibra_id,
        "vibra_ver": grupo.vibra_ver or 1,
        "consolidado_status": grupo.consolidado_status,
        "parecer_consolidado": grupo.parecer_consolidado,
        "score_grupo": grupo.score_grupo,
        "limite_consolidado": grupo.limite_consolidado,
        "limite_soma_individual": grupo.limite_soma_individual,
        "intercompany_obs": grupo.intercompany_obs,
        "created_at": grupo.created_at.isoformat() if grupo.created_at else None,
        "empresas": [
            {
                "analysis_id": a.id,
                "ordem": a.ordem_no_grupo,
                "company_name": a.company_name,
                "cnpj": a.cnpj,
                "status": a.status,
                "vibra_id": a.vibra_id,
            }
            for a in empresas
        ],
        "docs_consolidados": [
            {
                "id": d.id,
                "field_key": d.field_key,
                "field_label": d.field_label,
                "original_name": d.original_name,
                "file_url": f"/api/grupos/{grupo_id}/file/{_safe_name_grupo(d)}",
                "is_valid": d.is_valid,
                "validation_msg": d.validation_msg,
                "read_pct": d.read_pct,
                "doc_type_found": d.doc_type_found,
            }
            for d in docs
        ],
    }


@router.get("/list-by-client/{client_id}")
async def listar_grupos_por_client(
    client_id: str,
    caller_email: str = "",
    db: Session = Depends(get_db),
):
    """Lista grupos de um tenant. Use 'all' para listar todos (superadmin)."""
    from models.database import Usuario
    q = db.query(Grupo)

    if client_id and client_id != "all":
        # filtro explícito por client_id
        q = q.filter(Grupo.client_id == client_id)
    elif caller_email:
        # inferir filtro por el usuario caller
        caller = db.query(Usuario).filter(Usuario.email == caller_email).first()
        if caller and caller.perfil != "superadmin":
            q = q.filter(Grupo.client_id == caller.client_id)
        # superadmin sin client_id explícito: ve todos (no filtra)

    grupos = q.order_by(Grupo.created_at.desc()).all()

    out = []
    for g in grupos:
        count_empresas = db.query(Analysis).filter(Analysis.grupo_id == g.id).count()
        out.append({
            "id": g.id,
            "nome": g.nome,
            "vibra_id": g.vibra_id,
            "consolidado_status": g.consolidado_status,
            "score_grupo": g.score_grupo,
            "limite_consolidado": g.limite_consolidado,
            "qtd_empresas": count_empresas,
            "created_at": g.created_at.isoformat() if g.created_at else None,
        })
    return out


@router.patch("/{grupo_id}")
async def atualizar_grupo(
    grupo_id: str,
    db: Session = Depends(get_db),
    nome: str = Body(default=None, embed=True),
    analista: str = Body(default=None, embed=True),
    diretrizes: str = Body(default=None, embed=True),
    intercompany_obs: str = Body(default=None, embed=True),
):
    """Atualiza dados editáveis do grupo (nome, analista, diretrizes, intercompany)."""
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    if nome is not None:
        if not nome.strip():
            raise HTTPException(400, "Nome não pode ficar vazio")
        grupo.nome = nome.strip()
    if analista is not None:
        grupo.analista = analista
    if diretrizes is not None:
        grupo.diretrizes = diretrizes
    if intercompany_obs is not None:
        grupo.intercompany_obs = intercompany_obs

    db.commit()
    return {"updated": True}


@router.delete("/{grupo_id}")
async def excluir_grupo(grupo_id: str, db: Session = Depends(get_db)):
    """
    Exclui o grupo. Por segurança, desvincula as análises filhas em vez
    de cascateá-las — elas viram análises singulares órfãs (grupo_id = NULL).
    Os docs consolidados são removidos junto (cascade).
    """
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    empresas = db.query(Analysis).filter(Analysis.grupo_id == grupo_id).all()
    for emp in empresas:
        emp.grupo_id = None
        emp.ordem_no_grupo = None

    db.delete(grupo)
    db.commit()
    return {"deleted": True, "empresas_desvinculadas": len(empresas)}


# ── EMPRESAS FILHAS ───────────────────────────────────────────────

@router.post("/{grupo_id}/add-empresa")
async def adicionar_empresa(grupo_id: str, db: Session = Depends(get_db)):
    """
    Cria nova análise filha vinculada ao grupo.
    Hard-cap de 4 empresas (decisão 8).
    A análise criada é uma Analysis normal — pode ser usada nos endpoints
    /api/documents/{analysis_id}/upload e /api/analysis/{analysis_id}/run.
    """
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    count_atual = db.query(Analysis).filter(Analysis.grupo_id == grupo_id).count()
    if count_atual >= MAX_EMPRESAS_POR_GRUPO:
        raise HTTPException(
            400,
            f"Limite de {MAX_EMPRESAS_POR_GRUPO} empresas por grupo atingido"
        )

    nova = Analysis(
        grupo_id=grupo_id,
        ordem_no_grupo=count_atual + 1,
        client_id=grupo.client_id,   # herda tenant do grupo
    )
    db.add(nova)
    db.commit()
    db.refresh(nova)

    return {
        "analysis_id": nova.id,
        "ordem": nova.ordem_no_grupo,
        "grupo_id": grupo_id,
    }


@router.delete("/{grupo_id}/empresa/{analysis_id}")
async def remover_empresa_do_grupo(
    grupo_id: str, analysis_id: str, db: Session = Depends(get_db)
):
    """
    Remove o vínculo entre análise e grupo. A análise NÃO é excluída —
    ela vira singular (grupo_id = NULL). Para excluir totalmente, use a
    rota de exclusão de análise (a definir conforme o frontend).
    """
    analysis = (
        db.query(Analysis)
        .filter(Analysis.id == analysis_id, Analysis.grupo_id == grupo_id)
        .first()
    )
    if not analysis:
        raise HTTPException(404, "Análise não encontrada neste grupo")

    analysis.grupo_id = None
    analysis.ordem_no_grupo = None
    db.commit()
    return {"unlinked": True, "analysis_id": analysis_id}


# ── ID VIBRA DO GRUPO (G + AAMM-NN) ───────────────────────────────

def _gerar_vibra_id_grupo(db: Session) -> str:
    """
    Gera o próximo ID Vibra de grupo no formato G + AAMM-NN.
    Sequencial mensal independente do ID Vibra singular.
    """
    now = datetime.utcnow()
    prefixo = "G" + now.strftime("%y%m")  # ex: G2606

    count = (
        db.query(Grupo).filter(Grupo.vibra_id.like(prefixo + "-%")).count()
    )
    seq = count + 1
    return f"{prefixo}-{str(seq).zfill(2)}"


@router.post("/{grupo_id}/vibra-id")
async def criar_vibra_id_grupo(grupo_id: str, db: Session = Depends(get_db)):
    """Gera ID Vibra do grupo (idempotente — se já existir, retorna o existente)."""
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    if not grupo.vibra_id:
        grupo.vibra_id = _gerar_vibra_id_grupo(db)
        grupo.vibra_ver = 1
        db.commit()
        db.refresh(grupo)

    return {"vibra_id": grupo.vibra_id, "vibra_ver": grupo.vibra_ver or 1}


@router.post("/{grupo_id}/vibra-id/incrementar")
async def incrementar_vibra_ver_grupo(grupo_id: str, db: Session = Depends(get_db)):
    """Incrementa a versão do ID Vibra do grupo (uso em reabertura/reprocessamento)."""
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    if not grupo.vibra_id:
        grupo.vibra_id = _gerar_vibra_id_grupo(db)
        grupo.vibra_ver = 1
    else:
        grupo.vibra_ver = (grupo.vibra_ver or 1) + 1

    db.commit()
    db.refresh(grupo)
    return {"vibra_id": grupo.vibra_id, "vibra_ver": grupo.vibra_ver}


@router.get("/{grupo_id}/vibra-id")
async def get_vibra_id_grupo(grupo_id: str, db: Session = Depends(get_db)):
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")
    return {"vibra_id": grupo.vibra_id, "vibra_ver": grupo.vibra_ver or 1}


# ── DOCUMENTOS CONSOLIDADOS (vinculados ao grupo) ─────────────────

@router.post("/{grupo_id}/upload-consolidado")
async def upload_consolidado(
    grupo_id: str,
    field_key: str = Form(...),
    field_label: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Sobe documento consolidado do grupo (vale para todas as empresas filhas).
    Reusa validate_document_with_ai sem modificação (mesma validação dos docs singulares).
    """
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    existing = (
        db.query(GrupoDocument)
        .filter(
            GrupoDocument.grupo_id == grupo_id,
            GrupoDocument.field_key == field_key,
        )
        .count()
    )
    if existing >= MAX_FILES_PER_FIELD:
        raise HTTPException(
            400, f"Limite de {MAX_FILES_PER_FIELD} arquivos por campo atingido"
        )

    file_bytes = await file.read()
    mime_type = file.content_type or "application/octet-stream"

    local_dir = os.path.join(UPLOAD_DIR_GRUPOS, grupo_id)
    os.makedirs(local_dir, exist_ok=True)

    file_index = existing + 1
    safe_name = f"{field_key}_{file_index}_{file.filename}"
    local_path = os.path.join(local_dir, safe_name)
    with open(local_path, "wb") as f:
        f.write(file_bytes)

    file_url = f"/api/grupos/{grupo_id}/file/{safe_name}"

    # Reusa validador existente (sem modificação)
    validation = validate_document_with_ai(
        field_key=field_key,
        file_bytes=file_bytes,
        filename=file.filename,
        mime_type=mime_type,
    )

    doc = GrupoDocument(
        grupo_id=grupo_id,
        field_key=field_key,
        field_label=field_label,
        original_name=file.filename,
        s3_key=f"local:{safe_name}",
        file_size=len(file_bytes),
        mime_type=mime_type,
        is_valid=validation["is_valid"],
        validation_msg=validation["message"],
        read_pct=validation.get("read_pct"),
        doc_type_found=validation.get("doc_type_found"),
        is_required=False,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {
        "document_id": doc.id,
        "file_url": file_url,
        "file_index": file_index,
        "is_valid": validation["is_valid"],
        "doc_type_found": validation.get("doc_type_found"),
        "read_pct": validation.get("read_pct"),
        "compatibility_pct": validation.get("compatibility_pct"),
        "compatibility_level": validation.get("compatibility_level"),
        "compatibility_msg": validation.get("compatibility_msg"),
        "defasagem": validation.get("defasagem", {}),
        "data_referencia": validation.get("data_referencia"),
        "observacoes": validation.get("observacoes", ""),
        "message": validation["message"],
    }


@router.get("/{grupo_id}/consolidado-docs/list")
async def listar_docs_consolidados(grupo_id: str, db: Session = Depends(get_db)):
    """Lista todos os documentos consolidados do grupo."""
    docs = db.query(GrupoDocument).filter(GrupoDocument.grupo_id == grupo_id).all()
    out = []
    for d in docs:
        safe_name = _safe_name_grupo(d)
        out.append({
            "id": d.id,
            "field_key": d.field_key,
            "field_label": d.field_label,
            "original_name": d.original_name,
            "file_url": f"/api/grupos/{grupo_id}/file/{safe_name}",
            "is_valid": d.is_valid,
            "validation_msg": d.validation_msg,
            "read_pct": d.read_pct,
            "doc_type_found": d.doc_type_found,
        })
    return out


@router.get("/{grupo_id}/file/{filename}")
async def servir_arquivo_grupo(grupo_id: str, filename: str):
    """Serve um arquivo consolidado para visualização inline ou download."""
    local_dir = os.path.join(UPLOAD_DIR_GRUPOS, grupo_id)
    local_path = os.path.join(local_dir, filename)

    # Segurança: garantir que está dentro do diretório correto
    real_path = os.path.realpath(local_path)
    real_dir = os.path.realpath(local_dir)
    if not real_path.startswith(real_dir):
        raise HTTPException(403, "Acesso negado")

    if not os.path.exists(local_path):
        raise HTTPException(404, "Arquivo não encontrado")

    ext = filename.lower().split(".")[-1]
    media_types = {
        "pdf": "application/pdf",
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xls": "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FR(path=local_path, media_type=media_type, filename=filename)


@router.delete("/{grupo_id}/consolidado-doc/{doc_id}")
async def remover_doc_consolidado(
    grupo_id: str, doc_id: str, db: Session = Depends(get_db)
):
    """Remove um documento consolidado do grupo."""
    doc = (
        db.query(GrupoDocument)
        .filter(GrupoDocument.id == doc_id, GrupoDocument.grupo_id == grupo_id)
        .first()
    )
    if not doc:
        raise HTTPException(404, "Documento não encontrado")
    db.delete(doc)
    db.commit()
    return {"deleted": True}


# ── RODADA Q — ORQUESTRAÇÃO DAS ANÁLISES FILHAS ────────────────

UPLOAD_DIR_ANALISES = os.path.join(os.path.dirname(__file__), "..", "uploads")
CONSOLIDADO_PREFIX = "gc_"   # gc = grupo consolidado (prefixo no s3_key local)


def _materializar_consolidados_para_filha(
    db: Session, grupo_id: str, analysis_id: str
) -> int:
    """
    Copia fisicamente os arquivos consolidados do grupo para a pasta de uploads
    da análise filha e cria entries Document correspondentes. Idempotente:
    se um doc consolidado já foi materializado para esta análise, não duplica.

    Retorna a quantidade de docs materializados (ou já presentes).
    """
    src_dir = os.path.join(os.path.dirname(__file__), "..", "uploads_grupos", grupo_id)
    dst_dir = os.path.join(UPLOAD_DIR_ANALISES, analysis_id)
    os.makedirs(dst_dir, exist_ok=True)

    docs_consolidados = (
        db.query(GrupoDocument).filter(GrupoDocument.grupo_id == grupo_id).all()
    )

    materializados = 0
    for gdoc in docs_consolidados:
        if not gdoc.s3_key or not gdoc.s3_key.startswith("local:"):
            continue
        src_name = gdoc.s3_key[6:]  # remove "local:"
        src_path = os.path.join(src_dir, src_name)
        if not os.path.exists(src_path):
            continue

        # Nome destino com prefixo identificável (gc_ + nome original)
        dst_name = f"{CONSOLIDADO_PREFIX}{src_name}"
        dst_s3_key = f"local:{dst_name}"

        # Idempotência: se já existe Document apontando para esse s3_key, pula
        existing = (
            db.query(Document)
            .filter(
                Document.analysis_id == analysis_id,
                Document.s3_key == dst_s3_key,
            )
            .first()
        )
        if existing:
            materializados += 1
            continue

        # Copia o arquivo físico
        dst_path = os.path.join(dst_dir, dst_name)
        if not os.path.exists(dst_path):
            shutil.copy2(src_path, dst_path)

        # Cria Document espelhando o GrupoDocument
        novo = Document(
            analysis_id=analysis_id,
            field_key=gdoc.field_key,
            field_label=f"[Consolidado] {gdoc.field_label or gdoc.field_key}",
            original_name=gdoc.original_name,
            s3_key=dst_s3_key,
            file_size=gdoc.file_size,
            mime_type=gdoc.mime_type,
            is_valid=gdoc.is_valid,
            validation_msg=gdoc.validation_msg,
            read_pct=gdoc.read_pct,
            doc_type_found=gdoc.doc_type_found,
            is_required=False,
        )
        db.add(novo)
        materializados += 1

    db.commit()
    return materializados


def _orquestrar_run_all(grupo_id: str):
    """
    Executa em background a sequência de análises das empresas filhas.

    Patch da Rodada Q (fix):
    - Cada empresa usa sessão própria (isolamento contra contaminação).
    - Retry com backoff em caso de erro transitório no streaming Anthropic
      (httpx.RemoteProtocolError e similares).
    - Delay de 8s entre empresas para respeitar rate limit TPM da Anthropic.
    - Logs explícitos em stderr para diagnóstico em tempo real.
    - Empresas sem documentos viram 'error' direto, sem chamar a IA com lista vazia.
    """
    tag = f"[GRUPO {grupo_id[:8]}]"
    print(f"\n{tag} === Iniciando orquestração ===", flush=True)

    # Sessão raiz só para descobrir a lista de empresas e diretrizes
    db_root = SessionLocal()
    try:
        grupo = db_root.query(Grupo).filter(Grupo.id == grupo_id).first()
        if not grupo:
            print(f"{tag} grupo não encontrado, abortando", flush=True)
            return
        diretrizes = grupo.diretrizes or ""
        empresas_ids = [
            e.id for e in db_root.query(Analysis)
                .filter(Analysis.grupo_id == grupo_id)
                .order_by(asc(Analysis.ordem_no_grupo))
                .all()
        ]
    finally:
        db_root.close()

    total = len(empresas_ids)
    print(f"{tag} empresas a processar: {total}", flush=True)

    MAX_TENTATIVAS = 3
    DELAY_ENTRE_EMPRESAS = 8  # segundos
    ERROS_TRANSITORIOS = (
        "RemoteProtocolError",
        "incomplete chunked read",
        "ConnectionError",
        "ReadTimeout",
        "PoolTimeout",
        "ConnectTimeout",
        "rate_limit",
        "overloaded",
    )

    for idx, analysis_id in enumerate(empresas_ids, start=1):
        print(f"\n{tag} --- empresa {idx}/{total} (id={analysis_id[:8]}) ---", flush=True)

        # Sessão própria para cada empresa
        db = SessionLocal()
        try:
            # 1) Materializa docs consolidados (idempotente)
            try:
                qtd = _materializar_consolidados_para_filha(db, grupo_id, analysis_id)
                print(f"{tag}   consolidados materializados/presentes: {qtd}", flush=True)
            except Exception as e:
                print(f"{tag}   ERRO ao materializar consolidados: {type(e).__name__}: {e}", flush=True)
                try:
                    db.rollback()
                except Exception:
                    pass

            # 2) Busca documents desta empresa
            documents = (
                db.query(Document).filter(Document.analysis_id == analysis_id).all()
            )
            print(f"{tag}   total de documents: {len(documents)}", flush=True)

            if not documents:
                print(f"{tag}   empresa SEM documentos — marcando como 'error'", flush=True)
                try:
                    emp = db.query(Analysis).filter(Analysis.id == analysis_id).first()
                    if emp:
                        emp.status = "error"
                        db.commit()
                except Exception:
                    db.rollback()
                continue

            # 3) Tenta análise com retry
            sucesso = False
            for tentativa in range(1, MAX_TENTATIVAS + 1):
                try:
                    print(f"{tag}   rodando análise (tentativa {tentativa}/{MAX_TENTATIVAS})...", flush=True)
                    run_full_analysis(analysis_id, documents, db, diretrizes)
                    print(f"{tag}   ✓ análise concluída com sucesso", flush=True)
                    sucesso = True
                    break
                except Exception as e:
                    err_name = type(e).__name__
                    err_msg = str(e)[:200]
                    print(f"{tag}   ✗ erro tentativa {tentativa}: {err_name}: {err_msg}", flush=True)

                    transitorio = any(t in err_name or t in err_msg for t in ERROS_TRANSITORIOS)
                    if tentativa < MAX_TENTATIVAS and transitorio:
                        backoff = 5 * tentativa + random.uniform(0, 2)
                        print(f"{tag}   erro transitório, aguardando {backoff:.1f}s antes de retry...", flush=True)
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        time.sleep(backoff)
                        # Recarrega documents para próxima tentativa
                        try:
                            documents = db.query(Document).filter(
                                Document.analysis_id == analysis_id
                            ).all()
                        except Exception:
                            pass
                    else:
                        # Erro não transitório ou esgotou tentativas
                        print(f"{tag}   marcando empresa como 'error' definitivamente", flush=True)
                        traceback.print_exc()
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        try:
                            emp = db.query(Analysis).filter(
                                Analysis.id == analysis_id
                            ).first()
                            if emp:
                                emp.status = "error"
                                db.commit()
                        except Exception:
                            db.rollback()
                        break

            # 4) Delay antes da próxima empresa (rate limit)
            if idx < total:
                print(f"{tag}   aguardando {DELAY_ENTRE_EMPRESAS}s antes da próxima empresa...", flush=True)
                time.sleep(DELAY_ENTRE_EMPRESAS)
        finally:
            db.close()

    print(f"\n{tag} === Orquestração concluída ===\n", flush=True)

    # ── RODADA R — auto-dispara consolidação se ≥2 filhas estão 'done' ──
    db_check = SessionLocal()
    try:
        filhas_done = (
            db_check.query(Analysis)
            .filter(Analysis.grupo_id == grupo_id, Analysis.status == "done")
            .count()
        )
        if filhas_done >= 2:
            print(f"{tag} {filhas_done} filhas em 'done' — disparando consolidação automática...", flush=True)
            try:
                run_consolidated_analysis(grupo_id)
                print(f"{tag} ✓ consolidação automática concluída", flush=True)
            except Exception:
                print(f"{tag} ✗ falha na consolidação automática (frontend pode tentar manualmente)", flush=True)
                traceback.print_exc()
        else:
            print(f"{tag} apenas {filhas_done} filha(s) em 'done' — consolidação não auto-disparada (mín. 2)", flush=True)
    finally:
        db_check.close()


@router.post("/{grupo_id}/run-all")
async def run_all(
    grupo_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Dispara as análises das empresas filhas em sequência (background).
    Decisão 7: sequencial, para respeitar o rate limit Anthropic.
    Cada filha herda as diretrizes do grupo.
    """
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    empresas = db.query(Analysis).filter(Analysis.grupo_id == grupo_id).all()
    if not empresas:
        raise HTTPException(400, "Grupo não tem empresas cadastradas")

    # Marca todas como 'processing' para feedback imediato no frontend
    for emp in empresas:
        if emp.status not in ("done",):
            emp.status = "processing"
    db.commit()

    background_tasks.add_task(_orquestrar_run_all, grupo_id)

    return {
        "started": True,
        "grupo_id": grupo_id,
        "total_empresas": len(empresas),
    }


@router.get("/{grupo_id}/run-status")
async def run_status(grupo_id: str, db: Session = Depends(get_db)):
    """
    Retorna o status agregado da execução das análises filhas.
    Usado pelo frontend para polling durante /run-all.
    """
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    empresas = (
        db.query(Analysis)
        .filter(Analysis.grupo_id == grupo_id)
        .order_by(asc(Analysis.ordem_no_grupo))
        .all()
    )

    counts = {"pending": 0, "processing": 0, "done": 0, "error": 0}
    detalhes = []
    for emp in empresas:
        st = emp.status or "pending"
        if st not in counts:
            counts[st] = 0
        counts[st] += 1
        detalhes.append({
            "analysis_id": emp.id,
            "ordem": emp.ordem_no_grupo,
            "company_name": emp.company_name,
            "cnpj": emp.cnpj,
            "status": st,
            "vibra_id": emp.vibra_id,
        })

    total = len(empresas)
    todas_concluidas = counts["done"] + counts["error"] == total and total > 0
    progresso_pct = round((counts["done"] / total) * 100, 1) if total else 0

    return {
        "grupo_id": grupo_id,
        "total": total,
        "counts": counts,
        "progresso_pct": progresso_pct,
        "todas_concluidas": todas_concluidas,
        "empresas": detalhes,
    }


# ── RODADA R — CONSOLIDAÇÃO IA DO GRUPO ───────────────────────────

def _executar_consolidacao_bg(grupo_id: str):
    """Wrapper para background task — captura exceções para não derrubar o worker."""
    try:
        run_consolidated_analysis(grupo_id)
    except Exception:
        traceback.print_exc()


@router.post("/{grupo_id}/consolidate")
async def consolidate_grupo(
    grupo_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Dispara a consolidação IA do grupo em background.
    Pré-condição: pelo menos 2 empresas filhas em status 'done'.
    """
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    if grupo.consolidado_status == "processing":
        raise HTTPException(409, "Consolidação já em andamento")

    filhas_done = db.query(Analysis).filter(
        Analysis.grupo_id == grupo_id,
        Analysis.status == "done"
    ).count()

    if filhas_done < 2:
        raise HTTPException(
            400,
            f"Mínimo 2 empresas concluídas para consolidar (atual: {filhas_done})."
        )

    grupo.consolidado_status = "processing"
    db.commit()

    background_tasks.add_task(_executar_consolidacao_bg, grupo_id)

    return {
        "started": True,
        "grupo_id": grupo_id,
        "filhas_done": filhas_done,
    }


@router.get("/{grupo_id}/consolidate-status")
async def consolidate_status(grupo_id: str, db: Session = Depends(get_db)):
    """Retorna o status da consolidação + JSON completo se disponível."""
    grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
    if not grupo:
        raise HTTPException(404, "Grupo não encontrado")

    raw = None
    if grupo.consolidado_raw_json:
        try:
            raw = json.loads(grupo.consolidado_raw_json)
        except Exception:
            raw = None

    return {
        "grupo_id": grupo_id,
        "status": grupo.consolidado_status or "pending",
        "score_grupo": grupo.score_grupo,
        "limite_consolidado": grupo.limite_consolidado,
        "limite_soma_individual": grupo.limite_soma_individual,
        "parecer_consolidado": grupo.parecer_consolidado,
        "consolidado": raw,
    }
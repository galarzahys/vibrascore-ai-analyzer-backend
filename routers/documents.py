"""
Router — Documents
Upload múltiplo (até 5 por campo), visualização de arquivos, validação IA
"""

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import FileResponse as FR
from sqlalchemy.orm import Session
from models.database import get_db, Analysis, Document
from services.document_validator import validate_document_with_ai
import uuid, os, json

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
MAX_FILES_PER_FIELD = 5

# Documentos hard-stop
HARD_REQUIRED = {"bureau", "scr", "faturamento"}


@router.post("/create-analysis")
async def create_analysis(db: Session = Depends(get_db)):
    analysis = Analysis(id=str(uuid.uuid4()))
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return {"analysis_id": analysis.id}


@router.post("/{analysis_id}/upload")
async def upload_document(
    analysis_id: str,
    field_key: str = Form(...),
    field_label: str = Form(...),
    is_required: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Analise nao encontrada")

    # Verificar limite de arquivos por campo
    existing = db.query(Document).filter(
        Document.analysis_id == analysis_id,
        Document.field_key == field_key
    ).count()
    if existing >= MAX_FILES_PER_FIELD:
        raise HTTPException(400, f"Limite de {MAX_FILES_PER_FIELD} arquivos por campo atingido")

    file_bytes = await file.read()
    mime_type = file.content_type or "application/octet-stream"

    # Salvar em disco com nome único para evitar colisões
    local_dir = os.path.join(UPLOAD_DIR, analysis_id)
    os.makedirs(local_dir, exist_ok=True)
    
    # Incluir índice para múltiplos arquivos do mesmo campo
    file_index = existing + 1
    safe_name = f"{field_key}_{file_index}_{file.filename}"
    local_path = os.path.join(local_dir, safe_name)
    with open(local_path, "wb") as f:
        f.write(file_bytes)

    # URL de acesso ao arquivo
    file_url = f"/api/documents/{analysis_id}/file/{safe_name}"

    # Validar com Claude
    validation = validate_document_with_ai(
        field_key=field_key,
        file_bytes=file_bytes,
        filename=file.filename,
        mime_type=mime_type,
    )

    # Tentar S3 (opcional)
    s3_key = None
    try:
        from services.s3_service import upload_file
        s3_key = upload_file(file_bytes, file.filename, analysis_id, field_key)
    except Exception:
        pass

    doc = Document(
        analysis_id=analysis_id,
        field_key=field_key,
        field_label=field_label,
        original_name=file.filename,
        s3_key=s3_key,
        file_size=len(file_bytes),
        mime_type=mime_type,
        is_valid=validation["is_valid"],
        validation_msg=validation["message"],
        read_pct=validation.get("read_pct"),
        doc_type_found=validation.get("doc_type_found"),
        is_required=is_required,
    )
    # Guardar o caminho local no s3_key se não tiver S3
    if not doc.s3_key:
        doc.s3_key = f"local:{safe_name}"
    
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
        "is_balancete": validation.get("is_balancete", False),
        "has_dre_together": validation.get("has_dre_together", False),
        "observacoes": validation.get("observacoes", ""),
        "message": validation["message"],
    }


@router.get("/{analysis_id}/file/{filename}")
async def serve_file(analysis_id: str, filename: str):
    """Serve um arquivo de upload para visualização."""
    local_dir = os.path.join(UPLOAD_DIR, analysis_id)
    local_path = os.path.join(local_dir, filename)
    
    # Segurança: garantir que o path está dentro do diretório correto
    real_path = os.path.realpath(local_path)
    real_dir = os.path.realpath(local_dir)
    if not real_path.startswith(real_dir):
        raise HTTPException(403, "Acesso negado")
    
    if not os.path.exists(local_path):
        raise HTTPException(404, "Arquivo não encontrado")
    
    # Determinar media_type
    ext = filename.lower().split('.')[-1]
    media_types = {
        'pdf': 'application/pdf',
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png',
        'doc': 'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls': 'application/vnd.ms-excel',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }
    media_type = media_types.get(ext, 'application/octet-stream')
    
    return FR(path=local_path, media_type=media_type, filename=filename)


@router.get("/{analysis_id}/list")
async def list_documents(analysis_id: str, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.analysis_id == analysis_id).all()
    result = []
    for d in docs:
        # Reconstruir URL do arquivo
        safe_name = None
        if d.s3_key and d.s3_key.startswith("local:"):
            safe_name = d.s3_key[6:]
        else:
            # Fallback: nome antigo
            safe_name = d.field_key + "_" + d.original_name
        
        file_url = f"/api/documents/{analysis_id}/file/{safe_name}" if safe_name else None
        
        result.append({
            "id": d.id,
            "field_key": d.field_key,
            "field_label": d.field_label,
            "original_name": d.original_name,
            "file_url": file_url,
            "is_valid": d.is_valid,
            "validation_msg": d.validation_msg,
            "read_pct": d.read_pct,
            "doc_type_found": d.doc_type_found,
            "is_required": d.is_required,
        })
    return result


@router.post("/{analysis_id}/historico-interno")
async def salvar_historico_interno(
    analysis_id: str,
    db: Session = Depends(get_db),
    tempo_relacionamento: str = Form(""),
    volume_medio: str = Form(""),
    pontualidade: str = Form(""),
    inadimplencia: str = Form(""),
    operacoes: str = Form(""),
    obs: str = Form(""),
):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")
    historico = {
        "tempo_relacionamento": tempo_relacionamento,
        "volume_medio": volume_medio,
        "pontualidade": pontualidade,
        "inadimplencia": inadimplencia,
        "operacoes": operacoes,
        "obs": obs,
    }
    analysis.historico_interno = json.dumps(historico, ensure_ascii=False)
    db.commit()
    return {"saved": True}


@router.get("/{analysis_id}/historico-interno")
async def get_historico_interno(analysis_id: str, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(404, "Análise não encontrada")
    if not analysis.historico_interno:
        return {}
    try:
        return json.loads(analysis.historico_interno)
    except Exception:
        return {}


@router.patch("/{analysis_id}/doc/{doc_id}/toggle-required")
async def toggle_required(analysis_id: str, doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.analysis_id == analysis_id
    ).first()
    if not doc:
        raise HTTPException(404, "Documento nao encontrado")
    if doc.field_key in HARD_REQUIRED and doc.is_required:
        raise HTTPException(400, f"O documento '{doc.field_key}' e obrigatorio")
    doc.is_required = not doc.is_required
    db.commit()
    return {"is_required": doc.is_required}


@router.delete("/{analysis_id}/doc/{doc_id}")
async def delete_document(analysis_id: str, doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(
        Document.id == doc_id,
        Document.analysis_id == analysis_id
    ).first()
    if not doc:
        raise HTTPException(404, "Documento nao encontrado")
    db.delete(doc)
    db.commit()
    return {"deleted": True}


@router.get("/{analysis_id}/check-ready")
async def check_ready(analysis_id: str, db: Session = Depends(get_db)):
    docs = db.query(Document).filter(Document.analysis_id == analysis_id).all()
    docs_by_key = {}
    for d in docs:
        if d.field_key not in docs_by_key:
            docs_by_key[d.field_key] = []
        docs_by_key[d.field_key].append(d)

    issues = []
    can_run = True

    for key in HARD_REQUIRED:
        if key not in docs_by_key:
            issues.append(f"Documento obrigatorio ausente: {key}")
            can_run = False
        elif all(d.is_valid == False for d in docs_by_key[key]):
            issues.append(f"Documento '{key}' invalido ou incompativel")
            can_run = False

    return {"can_run": can_run, "issues": issues, "warnings": []}

"""
Router — Clients (Rodada J — multi-usuário)
Gestão de clientes (admin Vibra) e usuários por cliente (analista/gestor/leitor).
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from models.database import get_db, Cliente, Usuario, Analysis
import uuid

router = APIRouter()

ROLES_VALIDAS = {"analista", "gestor", "leitor"}


def _gerar_seq_cliente(db) -> str:
    """Sequencial de cliente no formato C-001, C-002..."""
    count = db.query(Cliente).count()
    return f"C-{str(count + 1).zfill(3)}"


# ── CLIENTES (admin Vibra) ──────────────────────────────────────

@router.post("/clientes")
async def criar_cliente(
    db: Session = Depends(get_db),
    nome: str = Body(..., embed=True),
    cnpj: str = Body(default="", embed=True),
):
    if not nome or not nome.strip():
        raise HTTPException(400, "Nome do cliente é obrigatório")
    cliente = Cliente(
        id=str(uuid.uuid4()),
        nome=nome.strip(),
        cnpj=(cnpj or "").strip(),
        vibra_seq=_gerar_seq_cliente(db),
        ativo=True,
    )
    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    return {
        "id": cliente.id,
        "nome": cliente.nome,
        "cnpj": cliente.cnpj,
        "vibra_seq": cliente.vibra_seq,
        "ativo": cliente.ativo,
    }


@router.get("/clientes")
async def listar_clientes(db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.created_at.desc()).all()
    result = []
    for c in clientes:
        n_analises = db.query(Analysis).filter(Analysis.client_id == c.id).count()
        n_usuarios = db.query(Usuario).filter(Usuario.client_id == c.id).count()
        result.append({
            "id": c.id,
            "nome": c.nome,
            "cnpj": c.cnpj,
            "vibra_seq": c.vibra_seq,
            "ativo": c.ativo,
            "n_analises": n_analises,
            "n_usuarios": n_usuarios,
        })
    return result


@router.get("/clientes/{client_id}")
async def get_cliente(client_id: str, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")
    return {
        "id": c.id,
        "nome": c.nome,
        "cnpj": c.cnpj,
        "vibra_seq": c.vibra_seq,
        "ativo": c.ativo,
    }


@router.patch("/clientes/{client_id}/toggle-ativo")
async def toggle_ativo_cliente(client_id: str, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")
    c.ativo = not c.ativo
    db.commit()
    return {"id": c.id, "ativo": c.ativo}


# ── USUÁRIOS por cliente ────────────────────────────────────────

@router.post("/clientes/{client_id}/usuarios")
async def criar_usuario(
    client_id: str,
    db: Session = Depends(get_db),
    nome: str = Body(..., embed=True),
    role: str = Body(default="analista", embed=True),
):
    cliente = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not cliente:
        raise HTTPException(404, "Cliente não encontrado")
    role = (role or "analista").lower().strip()
    if role not in ROLES_VALIDAS:
        raise HTTPException(400, f"Role inválida. Use: {', '.join(ROLES_VALIDAS)}")
    if not nome or not nome.strip():
        raise HTTPException(400, "Nome do usuário é obrigatório")
    usuario = Usuario(
        id=str(uuid.uuid4()),
        client_id=client_id,
        nome=nome.strip(),
        role=role,
        ativo=True,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return {
        "id": usuario.id,
        "client_id": usuario.client_id,
        "nome": usuario.nome,
        "role": usuario.role,
        "ativo": usuario.ativo,
    }


@router.get("/clientes/{client_id}/usuarios")
async def listar_usuarios(client_id: str, db: Session = Depends(get_db)):
    usuarios = db.query(Usuario).filter(Usuario.client_id == client_id).order_by(Usuario.created_at.desc()).all()
    return [{
        "id": u.id,
        "nome": u.nome,
        "role": u.role,
        "ativo": u.ativo,
    } for u in usuarios]


@router.delete("/usuarios/{usuario_id}")
async def deletar_usuario(usuario_id: str, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado")
    db.delete(u)
    db.commit()
    return {"deleted": True}

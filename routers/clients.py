"""
Router — Clients (tenants y sus usuarios)
Solo el superadmin puede crear/gestionar clientes.
El admin del tenant puede gestionar usuarios de su empresa.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from models.database import get_db, Cliente, Usuario, Analysis

router = APIRouter()


def _gerar_seq(db) -> str:
    count = db.query(Cliente).count()
    return f"C-{str(count + 1).zfill(3)}"


def _cliente_dict(c: Cliente, db: Session) -> dict:
    n_analises = db.query(Analysis).filter(Analysis.client_id == c.id).count()
    n_usuarios = db.query(Usuario).filter(Usuario.client_id == c.id).count()
    return {
        "id": c.id,
        "nome": c.nome,
        "cnpj": c.cnpj or "",
        "vibra_seq": c.vibra_seq,
        "ativo": c.ativo,
        "n_analises": n_analises,
        "n_usuarios": n_usuarios,
    }


# ── CLIENTES ───────────────────────────────────────────────────

@router.get("/")
async def listar_clientes(db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.created_at.desc()).all()
    return [_cliente_dict(c, db) for c in clientes]


@router.post("/")
async def criar_cliente(
    db: Session = Depends(get_db),
    nome: str = Body(..., embed=True),
    cnpj: str = Body(default="", embed=True),
):
    if not nome or not nome.strip():
        raise HTTPException(400, "Nome do cliente é obrigatório")
    c = Cliente(
        id=str(uuid.uuid4()),
        nome=nome.strip(),
        cnpj=(cnpj or "").strip(),
        vibra_seq=_gerar_seq(db),
        ativo=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _cliente_dict(c, db)


@router.get("/{client_id}")
async def get_cliente(client_id: str, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")
    return _cliente_dict(c, db)


@router.patch("/{client_id}/toggle-ativo")
async def toggle_ativo(client_id: str, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")
    c.ativo = not c.ativo
    db.commit()
    return {"id": c.id, "ativo": c.ativo}


# ── USUARIOS DEL TENANT ────────────────────────────────────────

@router.get("/{client_id}/usuarios")
async def listar_usuarios_tenant(client_id: str, db: Session = Depends(get_db)):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")
    usuarios = db.query(Usuario).filter(
        Usuario.client_id == client_id
    ).order_by(Usuario.nome).all()
    return [{
        "id": u.id,
        "email": u.email,
        "nome": u.nome,
        "cargo": u.cargo or "",
        "perfil": u.perfil,
        "ativo": u.ativo,
    } for u in usuarios]


@router.post("/{client_id}/usuarios")
async def criar_usuario_tenant(
    client_id: str,
    db: Session = Depends(get_db),
    email: str = Body(..., embed=True),
    senha: str = Body(..., embed=True),
    nome: str = Body(..., embed=True),
    cargo: str = Body(default="", embed=True),
    perfil: str = Body(default="analista", embed=True),
):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")

    perfis_validos = {"admin", "gerente", "analista", "comite"}
    if perfil not in perfis_validos:
        raise HTTPException(400, f"Perfil inválido. Use: {', '.join(perfis_validos)}")

    email = email.strip().lower()
    if db.query(Usuario).filter(Usuario.email == email).first():
        raise HTTPException(400, "Já existe um usuário com este e-mail")

    u = Usuario(
        id=str(uuid.uuid4()),
        email=email,
        senha=senha,
        nome=nome.strip(),
        cargo=(cargo or "").strip(),
        perfil=perfil,
        client_id=client_id,
        ativo=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {
        "id": u.id,
        "email": u.email,
        "nome": u.nome,
        "cargo": u.cargo or "",
        "perfil": u.perfil,
        "client_id": u.client_id,
        "ativo": u.ativo,
    }



@router.patch("/{client_id}")
async def editar_cliente(
    client_id: str,
    db: Session = Depends(get_db),
    nome: str = Body(default=None, embed=True),
    cnpj: str = Body(default=None, embed=True),
):
    c = db.query(Cliente).filter(Cliente.id == client_id).first()
    if not c:
        raise HTTPException(404, "Cliente não encontrado")
    if nome is not None:
        c.nome = nome.strip()
    if cnpj is not None:
        c.cnpj = cnpj.strip()
    db.commit()
    db.refresh(c)
    return _cliente_dict(c, db)
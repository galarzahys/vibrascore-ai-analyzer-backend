"""
Router — Auth (login, sesión, gestión de usuarios)
Modelo multi-tenant:
  superadmin (client_id=NULL) — ve todo
  admin/gestor/analista/comite (client_id=UUID) — ve solo su tenant
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from models.database import get_db, Usuario

router = APIRouter()

# Superadmin de la plataforma — seed si la tabla está vacía
SUPERADMIN_DEFAULT = {
    "email": "admin@vibrascore.com.br",
    "senha": "admin2024",
    "nome": "Administrador Vibra",
    "cargo": "Administrador",
    "perfil": "superadmin",
    "client_id": None,
    "ativo": True,
}

PERFIS_VALIDOS = {"superadmin", "admin", "gestor", "analista", "comite"}


def _seed(db: Session):
    """Insere superadmin se a tabela estiver vazia."""
    if db.query(Usuario).count() == 0:
        db.add(Usuario(id=str(uuid.uuid4()), **SUPERADMIN_DEFAULT))
        db.commit()


def _usuario_dict(u: Usuario) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "usuario": u.email,        # compatibilidad frontend
        "nome": u.nome,
        "cargo": u.cargo or "",
        "perfil": u.perfil,
        "client_id": u.client_id,  # NULL = superadmin
        "ativo": u.ativo,
    }


# ── LOGIN ──────────────────────────────────────────────────────

@router.post("/login")
async def login(
    db: Session = Depends(get_db),
    email: str = Body(..., embed=True),
    senha: str = Body(..., embed=True),
):
    _seed(db)
    u = db.query(Usuario).filter(
        Usuario.email == email.strip().lower(),
        Usuario.senha == senha,
        Usuario.ativo == True,
    ).first()
    if not u:
        raise HTTPException(401, "Usuário ou senha inválidos")
    return _usuario_dict(u)


# ── SESIÓN ACTUAL ──────────────────────────────────────────────

@router.get("/me")
async def me(email: str = "", db: Session = Depends(get_db)):
    if not email:
        raise HTTPException(400, "Email requerido")
    _seed(db)
    u = db.query(Usuario).filter(
        Usuario.email == email.strip().lower(),
        Usuario.ativo == True,
    ).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado ou inativo")
    return _usuario_dict(u)


# ── LISTAR USUARIOS ────────────────────────────────────────────
# superadmin ve todos; admin ve solo los de su client_id

@router.get("/usuarios")
async def listar_usuarios(
    caller_email: str = "",
    db: Session = Depends(get_db),
):
    _seed(db)
    caller = db.query(Usuario).filter(Usuario.email == caller_email).first() if caller_email else None

    if caller and caller.perfil != "superadmin":
        # admin del tenant: solo ve usuarios de su empresa
        usuarios = db.query(Usuario).filter(
            Usuario.client_id == caller.client_id
        ).order_by(Usuario.nome).all()
    else:
        # superadmin: ve todos
        usuarios = db.query(Usuario).order_by(Usuario.nome).all()

    return [_usuario_dict(u) for u in usuarios]


# ── CREAR USUARIO ──────────────────────────────────────────────

@router.post("/usuarios")
async def criar_usuario(
    db: Session = Depends(get_db),
    email: str = Body(..., embed=True),
    senha: str = Body(..., embed=True),
    nome: str = Body(..., embed=True),
    cargo: str = Body(default="", embed=True),
    perfil: str = Body(default="analista", embed=True),
    client_id: str = Body(default=None, embed=True),
    caller_email: str = Body(default="", embed=True),
):
    # Verificar permisos del caller
    caller = db.query(Usuario).filter(Usuario.email == caller_email).first() if caller_email else None
    if caller and caller.perfil not in ("superadmin", "admin"):
        raise HTTPException(403, "Sem permissão para criar usuários")

    # Admin de tenant solo puede crear usuarios para su propio tenant
    if caller and caller.perfil == "admin":
        client_id = caller.client_id
        if perfil in ("superadmin",):
            raise HTTPException(403, "Não pode criar superadmin")

    email = email.strip().lower()
    if db.query(Usuario).filter(Usuario.email == email).first():
        raise HTTPException(400, "Já existe um usuário com este e-mail")
    if perfil not in PERFIS_VALIDOS:
        raise HTTPException(400, f"Perfil inválido. Use: {', '.join(PERFIS_VALIDOS)}")

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
    return _usuario_dict(u)


# ── EDITAR USUARIO ─────────────────────────────────────────────

@router.patch("/usuarios/{usuario_id}")
async def editar_usuario(
    usuario_id: str,
    db: Session = Depends(get_db),
    nome: str = Body(default=None, embed=True),
    cargo: str = Body(default=None, embed=True),
    perfil: str = Body(default=None, embed=True),
    senha: str = Body(default=None, embed=True),
    ativo: bool = Body(default=None, embed=True),
):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado")
    if nome is not None:
        u.nome = nome.strip()
    if cargo is not None:
        u.cargo = cargo.strip()
    if perfil is not None:
        if perfil not in PERFIS_VALIDOS:
            raise HTTPException(400, f"Perfil inválido. Use: {', '.join(PERFIS_VALIDOS)}")
        u.perfil = perfil
    if senha is not None and senha.strip():
        u.senha = senha.strip()
    if ativo is not None:
        u.ativo = ativo
    db.commit()
    db.refresh(u)
    return _usuario_dict(u)


# ── ELIMINAR USUARIO ───────────────────────────────────────────

@router.delete("/usuarios/{usuario_id}")
async def deletar_usuario(usuario_id: str, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u:
        raise HTTPException(404, "Usuário não encontrado")
    if u.perfil == "superadmin":
        raise HTTPException(403, "Não é possível deletar o superadmin")
    db.delete(u)
    db.commit()
    return {"deleted": True}

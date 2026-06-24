"""
migration_multitenant.py — Unifica modelo de usuarios y agrega soporte multi-tenant.

Ejecutar desde la carpeta backend:
    python migration_multitenant.py

Idempotente: puede ejecutarse múltiples veces sin daño.
"""

import sqlite3, os, glob, uuid


def encontrar_db():
    for p in ["vibrascore.db", "*.db", "data/*.db", "../*.db"]:
        for f in glob.glob(p):
            return f
    return None


def col_existe(cur, tabela, coluna):
    cur.execute(f"PRAGMA table_info({tabela})")
    return any(r[1] == coluna for r in cur.fetchall())


def tabela_existe(cur, tabela):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,))
    return cur.fetchone() is not None


def main():
    db_path = encontrar_db()
    if not db_path:
        print("ERRO: .db não encontrado. Rode da pasta backend.")
        return

    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # ── 1) tabela clientes ─────────────────────────────────────
    if tabela_existe(cur, "clientes"):
        print("[ok ] tabela clientes já existe")
    else:
        cur.execute("""
            CREATE TABLE clientes (
                id         TEXT PRIMARY KEY,
                created_at TEXT,
                updated_at TEXT,
                nome       TEXT NOT NULL,
                cnpj       TEXT,
                vibra_seq  TEXT,
                ativo      INTEGER DEFAULT 1
            )
        """)
        print("[add] tabela clientes criada")

    # ── 2) tabela usuarios — recriar con client_id ─────────────
    # Verificar si ya tiene client_id
    if tabela_existe(cur, "usuarios"):
        if col_existe(cur, "usuarios", "client_id"):
            print("[ok ] usuarios.client_id já existe")
        else:
            cur.execute("ALTER TABLE usuarios ADD COLUMN client_id TEXT")
            print("[add] usuarios.client_id adicionada")
        # garantir perfil superadmin existe
        if not col_existe(cur, "usuarios", "perfil"):
            cur.execute("ALTER TABLE usuarios ADD COLUMN perfil TEXT DEFAULT 'analista'")
            print("[add] usuarios.perfil adicionada")
    else:
        cur.execute("""
            CREATE TABLE usuarios (
                id         TEXT PRIMARY KEY,
                created_at TEXT,
                email      TEXT NOT NULL UNIQUE,
                senha      TEXT NOT NULL,
                nome       TEXT NOT NULL,
                cargo      TEXT,
                perfil     TEXT DEFAULT 'analista',
                client_id  TEXT,
                ativo      INTEGER DEFAULT 1
            )
        """)
        print("[add] tabela usuarios criada")

    # ── 3) seed superadmin si no existe ───────────────────────
    cur.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='superadmin'")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO usuarios (id, email, senha, nome, cargo, perfil, client_id, ativo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), "admin@vibrascore.com.br", "admin2024",
              "Administrador Vibra", "Administrador", "superadmin", None, 1))
        print("[add] superadmin criado: admin@vibrascore.com.br / admin2024")
    else:
        print("[ok ] superadmin já existe")

    # ── 4) tabela admin_config ─────────────────────────────────
    if tabela_existe(cur, "admin_config"):
        print("[ok ] admin_config já existe")
        for col, tipo in [("plataforma_nome","TEXT"),("score_min","INTEGER"),
                          ("admin_senha","TEXT"),("defasagem_json","TEXT")]:
            if not col_existe(cur, "admin_config", col):
                cur.execute(f"ALTER TABLE admin_config ADD COLUMN {col} {tipo}")
                print(f"[add] admin_config.{col}")
    else:
        cur.execute("""
            CREATE TABLE admin_config (
                id              INTEGER PRIMARY KEY,
                plataforma_nome TEXT DEFAULT 'VibraScore',
                score_min       INTEGER DEFAULT 0,
                admin_senha     TEXT DEFAULT 'vibra2024',
                defasagem_json  TEXT,
                updated_at      TEXT
            )
        """)
        cur.execute("INSERT INTO admin_config (id) VALUES (1)")
        print("[add] admin_config criada")

    # ── 5) reports: columnas nuevas ────────────────────────────
    for col in ["parecer_analista_json","comite_json","obs_json","feedback_json"]:
        if col_existe(cur, "reports", col):
            print(f"[ok ] reports.{col} já existe")
        else:
            cur.execute(f"ALTER TABLE reports ADD COLUMN {col} TEXT")
            print(f"[add] reports.{col}")

    conn.commit()
    conn.close()
    print("\n✓ Migration multitenant concluída.")


if __name__ == "__main__":
    main()

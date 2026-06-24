"""
migration_novos_campos.py — Agrega columnas y tablas necesarias para
eliminar la dependencia de localStorage.

Ejecutar desde la carpeta backend:
    python migration_novos_campos.py

Idempotente: puede ejecutarse múltiples veces sin daño.
"""

import sqlite3
import os
import glob


def encontrar_db():
    for padrao in ["vibrascore.db", "*.db", "data/*.db", "../*.db"]:
        for path in glob.glob(padrao):
            return path
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
        print("ERRO: arquivo .db não encontrado. Rode da pasta backend.")
        return

    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # ── 1) reports: 4 colunas novas ───────────────────────────
    novas_cols_reports = [
        ("parecer_analista_json", "TEXT"),  # reemplaza av_{aid}
        ("comite_json",           "TEXT"),  # reemplaza comite_{aid}
        ("obs_json",              "TEXT"),  # reemplaza obs_{aid}_{abaId}
        ("feedback_json",         "TEXT"),  # reemplaza fb_{aid}
    ]
    for col, tipo in novas_cols_reports:
        if col_existe(cur, "reports", col):
            print(f"[ok ] reports.{col} já existe")
        else:
            cur.execute(f"ALTER TABLE reports ADD COLUMN {col} {tipo}")
            print(f"[add] reports.{col} criada")

    # ── 2) tabela usuarios ─────────────────────────────────────
    if tabela_existe(cur, "usuarios"):
        print("[ok ] tabela usuarios já existe")
        # garantir que tiene columnas email, senha, cargo, perfil
        for col, tipo in [("email","VARCHAR"), ("senha","VARCHAR"),
                           ("cargo","VARCHAR"), ("perfil","VARCHAR"),
                           ("ativo","BOOLEAN")]:
            if not col_existe(cur, "usuarios", col):
                cur.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {tipo}")
                print(f"[add] usuarios.{col} criada")
    else:
        cur.execute("""
            CREATE TABLE usuarios (
                id      VARCHAR PRIMARY KEY,
                email   VARCHAR NOT NULL UNIQUE,
                senha   VARCHAR NOT NULL,
                nome    VARCHAR NOT NULL,
                cargo   VARCHAR,
                perfil  VARCHAR DEFAULT 'analista',
                ativo   BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT (datetime('now'))
            )
        """)
        print("[add] tabela usuarios criada")

    # ── 3) tabela admin_config (singleton) ────────────────────
    if tabela_existe(cur, "admin_config"):
        print("[ok ] tabela admin_config já existe")
        for col, tipo in [("plataforma_nome","VARCHAR"),
                           ("score_min","INTEGER"),
                           ("admin_senha","VARCHAR"),
                           ("defasagem_json","TEXT")]:
            if not col_existe(cur, "admin_config", col):
                cur.execute(f"ALTER TABLE admin_config ADD COLUMN {col} {tipo}")
                print(f"[add] admin_config.{col} criada")
    else:
        cur.execute("""
            CREATE TABLE admin_config (
                id              INTEGER PRIMARY KEY,
                plataforma_nome VARCHAR DEFAULT 'VibraScore',
                score_min       INTEGER DEFAULT 0,
                admin_senha     VARCHAR DEFAULT 'vibra2024',
                defasagem_json  TEXT,
                updated_at      DATETIME DEFAULT (datetime('now'))
            )
        """)
        cur.execute("INSERT INTO admin_config (id) VALUES (1)")
        print("[add] tabela admin_config criada com singleton")

    conn.commit()
    conn.close()
    print("\n✓ Migration concluída.")


if __name__ == "__main__":
    main()

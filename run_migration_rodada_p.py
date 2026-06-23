"""
Vibra Score — Executor da Migration da Rodada P

Uso (a partir da pasta backend, com o venv ativado):

    python run_migration_rodada_p.py

O script é idempotente: pode ser executado múltiplas vezes sem causar dano.
Operações já aplicadas são detectadas e puladas com aviso.
"""

import sqlite3
import os
import sys

# Caminho do banco — mesmo padrão usado em models/database.py
DB_PATH = os.path.join(os.path.dirname(__file__), "vibrascore.db")


def coluna_existe(cursor, tabela: str, coluna: str) -> bool:
    cursor.execute(f"PRAGMA table_info({tabela})")
    return any(row[1] == coluna for row in cursor.fetchall())


def tabela_existe(cursor, tabela: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (tabela,)
    )
    return cursor.fetchone() is not None


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERRO: banco nao encontrado em {DB_PATH}")
        print("Rode este script de dentro da pasta backend (onde fica vibrascore.db).")
        sys.exit(1)

    print(f"Banco encontrado: {DB_PATH}")
    print("Aplicando migration da Rodada P...\n")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ── 1) Colunas em analyses ────────────────────────────────
    if coluna_existe(cur, "analyses", "grupo_id"):
        print("[ok ] analyses.grupo_id ja existe — pulando")
    else:
        cur.execute("ALTER TABLE analyses ADD COLUMN grupo_id VARCHAR")
        print("[add] analyses.grupo_id criada")

    if coluna_existe(cur, "analyses", "ordem_no_grupo"):
        print("[ok ] analyses.ordem_no_grupo ja existe — pulando")
    else:
        cur.execute("ALTER TABLE analyses ADD COLUMN ordem_no_grupo INTEGER")
        print("[add] analyses.ordem_no_grupo criada")

    # ── 2) Tabela grupos ──────────────────────────────────────
    if tabela_existe(cur, "grupos"):
        print("[ok ] tabela grupos ja existe — pulando")
    else:
        cur.execute("""
            CREATE TABLE grupos (
                id                      VARCHAR PRIMARY KEY,
                created_at              DATETIME,
                updated_at              DATETIME,
                nome                    VARCHAR NOT NULL,
                client_id               VARCHAR,
                analista                VARCHAR,
                diretrizes              TEXT,
                vibra_id                VARCHAR,
                vibra_ver               INTEGER DEFAULT 1,
                consolidado_status      VARCHAR DEFAULT 'pending',
                consolidado_raw_json    TEXT,
                parecer_consolidado     TEXT,
                score_grupo             FLOAT,
                limite_consolidado      FLOAT,
                limite_soma_individual  FLOAT,
                intercompany_obs        TEXT
            )
        """)
        print("[add] tabela grupos criada")

    # ── 3) Tabela grupo_documents ─────────────────────────────
    if tabela_existe(cur, "grupo_documents"):
        print("[ok ] tabela grupo_documents ja existe — pulando")
    else:
        cur.execute("""
            CREATE TABLE grupo_documents (
                id              VARCHAR PRIMARY KEY,
                grupo_id        VARCHAR NOT NULL,
                created_at      DATETIME,
                field_key       VARCHAR NOT NULL,
                field_label     VARCHAR,
                original_name   VARCHAR NOT NULL,
                s3_key          VARCHAR,
                file_size       INTEGER,
                mime_type       VARCHAR,
                is_valid        BOOLEAN,
                validation_msg  TEXT,
                read_pct        FLOAT,
                doc_type_found  VARCHAR,
                is_required     BOOLEAN DEFAULT 0,
                FOREIGN KEY (grupo_id) REFERENCES grupos(id)
            )
        """)
        print("[add] tabela grupo_documents criada")

    # ── 4) Indices ────────────────────────────────────────────
    cur.execute("CREATE INDEX IF NOT EXISTS idx_analyses_grupo_id ON analyses(grupo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grupo_documents_grupo_id ON grupo_documents(grupo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grupos_client_id ON grupos(client_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_grupos_vibra_id ON grupos(vibra_id)")
    print("[ok ] indices criados/garantidos")

    conn.commit()

    # ── Verificacao final ─────────────────────────────────────
    print("\n=== Verificacao ===")
    cur.execute("PRAGMA table_info(analyses)")
    cols = [row[1] for row in cur.fetchall()]
    print(f"Colunas em analyses ({len(cols)}): {', '.join(cols)}")
    if "grupo_id" in cols and "ordem_no_grupo" in cols:
        print("  -> analyses esta correto")
    else:
        print("  -> FALTANDO colunas em analyses!")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tabelas = [row[0] for row in cur.fetchall()]
    print(f"Tabelas no banco: {', '.join(tabelas)}")
    if "grupos" in tabelas and "grupo_documents" in tabelas:
        print("  -> tabelas de grupo presentes")
    else:
        print("  -> FALTANDO tabelas de grupo!")

    conn.close()
    print("\nMigration concluida com sucesso.")


if __name__ == "__main__":
    main()

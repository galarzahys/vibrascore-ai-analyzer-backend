"""
migration_etapa2.py — Adiciona coluna overrides_json à tabela reports.

Roda da pasta backend:
    python migration_etapa2.py

Idempotente: se a coluna já existe, não faz nada.
"""

import sqlite3
import sys
import glob


def encontrar_db():
    for padrao in ["vibrascore.db", "*.db", "data/*.db", "../*.db"]:
        for path in glob.glob(padrao):
            return path
    return None


def main():
    db_path = encontrar_db()
    if not db_path:
        print("ERRO: arquivo .db não encontrado.")
        print("Rode este script da pasta backend.")
        sys.exit(1)

    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(reports)").fetchall()]
    print(f"Colunas atuais em reports: {len(cols)}")

    if "overrides_json" in cols:
        print("✓ Coluna overrides_json já existe. Nada a fazer.")
        conn.close()
        return

    print("Adicionando coluna overrides_json...")
    cur.execute("ALTER TABLE reports ADD COLUMN overrides_json TEXT")
    conn.commit()
    print("✓ Coluna overrides_json adicionada.")

    cols_depois = [r[1] for r in cur.execute("PRAGMA table_info(reports)").fetchall()]
    print(f"Colunas em reports agora: {len(cols_depois)}")

    conn.close()


if __name__ == "__main__":
    main()

"""
migration_reanalise_v2.py
Adiciona parent_analysis_id e version_num na tabela analyses.
Remove a abordagem de report_versions (substituída por analyses vinculadas).

Executar: python migration_reanalise_v2.py
"""
import sqlite3, glob, os


def encontrar_db():
    caminhos = ["/home/ubuntu/data/vibrascore.db", "vibrascore.db", "../vibrascore.db"]
    for p in caminhos:
        if os.path.exists(p): return p
    for padrao in ["*.db", "data/*.db"]:
        for f in glob.glob(padrao): return f
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
        print("ERRO: .db não encontrado.")
        return
    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # analyses: agregar parent_analysis_id e version_num
    for col, tipo, default in [
        ("parent_analysis_id", "TEXT", None),
        ("version_num", "INTEGER", 1),
    ]:
        if col_existe(cur, "analyses", col):
            print(f"[ok ] analyses.{col} já existe")
        else:
            if default is not None:
                cur.execute(f"ALTER TABLE analyses ADD COLUMN {col} {tipo} DEFAULT {default}")
            else:
                cur.execute(f"ALTER TABLE analyses ADD COLUMN {col} {tipo}")
            print(f"[add] analyses.{col}")

    # garantir que analyses existentes tenham version_num=1
    cur.execute("UPDATE analyses SET version_num=1 WHERE version_num IS NULL")
    print(f"[ok ] version_num=1 set para análises existentes")

    conn.commit()
    conn.close()
    print("Migration concluída.")


if __name__ == "__main__":
    main()

"""
migration_scoring_tenant.py
Agrega client_id a scoring_config para permitir calibração por tenant.
NULL = configuração global (superadmin).

Ejecutar desde backend: python migration_scoring_tenant.py
"""
import sqlite3, glob

def encontrar_db():
    for p in ["vibrascore.db", "*.db", "data/*.db", "../*.db"]:
        for f in glob.glob(p):
            return f
    return None

def col_existe(cur, tabela, coluna):
    cur.execute(f"PRAGMA table_info({tabela})")
    return any(r[1] == coluna for r in cur.fetchall())

def main():
    db_path = "/home/ubuntu/data/vibrascore.db"
    if not db_path:
        print("ERRO: .db não encontrado.")
        return
    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if col_existe(cur, "scoring_config", "client_id"):
        print("[ok ] scoring_config.client_id já existe")
    else:
        cur.execute("ALTER TABLE scoring_config ADD COLUMN client_id TEXT")
        print("[add] scoring_config.client_id adicionada")

    # remover constraint de id=1 fixo não é necessário em SQLite (sem PK autoincrement estrito)
    # mas garantir que o registro existente (id=1) continue como global (client_id=NULL)
    cur.execute("UPDATE scoring_config SET client_id = NULL WHERE id = 1 AND client_id IS NOT NULL")

    conn.commit()
    conn.close()
    print("Migration concluída.")

if __name__ == "__main__":
    main()

"""
migration_integrations.py
Cria tabela api_integrations para credenciais por tenant.
Executar: python migration_integrations.py
"""
import sqlite3, glob

def encontrar_db():
    for p in ["vibrascore.db", "*.db", "data/*.db", "../*.db",
              "/home/ubuntu/data/vibrascore.db"]:
        import glob as g
        for f in g.glob(p):
            return f
    return None

def main():
    db_path = "/home/ubuntu/data/vibrascore.db"
    if not db_path:
        print("ERRO: .db não encontrado.")
        return
    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_integrations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id  TEXT NOT NULL UNIQUE,
            api_key    TEXT,
            api_secret TEXT,
            sandbox    INTEGER DEFAULT 1,
            ativo      INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    print("[ok] tabela api_integrations criada")

    # também adicionar coluna integration_request_id em analyses para polling
    cur.execute("PRAGMA table_info(analyses)")
    cols = [r[1] for r in cur.fetchall()]
    if "integration_bureau_id" not in cols:
        cur.execute("ALTER TABLE analyses ADD COLUMN integration_bureau_id TEXT")
        print("[add] analyses.integration_bureau_id")
    if "integration_scr_id" not in cols:
        cur.execute("ALTER TABLE analyses ADD COLUMN integration_scr_id TEXT")
        print("[add] analyses.integration_scr_id")

    conn.commit()
    conn.close()
    print("Migration concluída.")

if __name__ == "__main__":
    main()

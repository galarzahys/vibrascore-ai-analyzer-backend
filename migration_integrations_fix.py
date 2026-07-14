"""
migration_integrations_fix.py
Corrige a tabela api_integrations para permitir client_id NULL
(necessário para suportar equipe interna sem tenant).

Executar: python migration_integrations_fix.py
Idempotente: pode rodar várias vezes sem dano.
"""
import sqlite3, glob, os


def encontrar_db():
    caminhos = [
        "/home/ubuntu/data/vibrascore.db",
        "vibrascore.db",
        "../vibrascore.db",
    ]
    for p in caminhos:
        if os.path.exists(p):
            return p
    for padrao in ["*.db", "data/*.db"]:
        for f in glob.glob(padrao):
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

    # verificar se a tabela existe
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='api_integrations'")
    if not cur.fetchone():
        # criar do zero já com client_id nullable
        cur.execute("""
            CREATE TABLE api_integrations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  TEXT UNIQUE,
                api_key    TEXT,
                api_secret TEXT,
                sandbox    INTEGER DEFAULT 0,
                ativo      INTEGER DEFAULT 1,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        print("[add] tabela api_integrations criada (client_id nullable)")
        conn.commit()
        conn.close()
        print("Migration concluída.")
        return

    # verificar se client_id já permite NULL
    cur.execute("PRAGMA table_info(api_integrations)")
    cols = cur.fetchall()
    client_id_col = next((c for c in cols if c[1] == "client_id"), None)
    if client_id_col and client_id_col[3] == 0:  # notnull=0 significa nullable
        print("[ok] client_id já é nullable. Nada a fazer.")
        conn.close()
        return

    print("[fix] Recriando tabela com client_id nullable...")

    # salvar dados existentes
    cur.execute("SELECT id, client_id, api_key, api_secret, sandbox, ativo, updated_at FROM api_integrations")
    rows = cur.fetchall()
    print(f"      {len(rows)} registro(s) a preservar")

    # recriar tabela
    cur.execute("ALTER TABLE api_integrations RENAME TO api_integrations_bkp")
    cur.execute("""
        CREATE TABLE api_integrations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id  TEXT UNIQUE,
            api_key    TEXT,
            api_secret TEXT,
            sandbox    INTEGER DEFAULT 0,
            ativo      INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # restaurar dados
    for row in rows:
        cur.execute(
            "INSERT INTO api_integrations (id, client_id, api_key, api_secret, sandbox, ativo, updated_at) VALUES (?,?,?,?,?,?,?)",
            row
        )

    cur.execute("DROP TABLE api_integrations_bkp")
    conn.commit()
    conn.close()
    print(f"[ok] Tabela recriada com {len(rows)} registro(s) preservado(s).")
    print("Migration concluída.")


if __name__ == "__main__":
    main()

r"""
Migration Etapa 5 — cria tabela scoring_config (singleton).
Idempotente: pode rodar várias vezes sem efeito colateral.

Uso:
    cd backend
    venv\Scripts\activate
    python migration_etapa5.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vibrascore.db")

DEFAULTS = {
    "peso_bureau":         25.0,
    "peso_financeiro":     25.0,
    "peso_comportamental": 15.0,
    "peso_cadastral":      10.0,
    "peso_tributario":     10.0,
    "peso_garantias":      10.0,
    "peso_cobertura":       5.0,
    "limite_a": 900, "limite_b": 800, "limite_c": 700, "limite_d": 600,
    "limite_e": 500, "limite_f": 400, "limite_g": 300, "limite_h": 200,
    "limite_i": 100,
}


def main():
    if not os.path.exists(DB_PATH):
        print(f"[MIGRATION ETAPA 5] Banco não encontrado em {DB_PATH}")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Cria tabela se não existir
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scoring_config (
            id INTEGER PRIMARY KEY,
            peso_bureau REAL DEFAULT 25.0,
            peso_financeiro REAL DEFAULT 25.0,
            peso_comportamental REAL DEFAULT 15.0,
            peso_cadastral REAL DEFAULT 10.0,
            peso_tributario REAL DEFAULT 10.0,
            peso_garantias REAL DEFAULT 10.0,
            peso_cobertura REAL DEFAULT 5.0,
            limite_a INTEGER DEFAULT 900,
            limite_b INTEGER DEFAULT 800,
            limite_c INTEGER DEFAULT 700,
            limite_d INTEGER DEFAULT 600,
            limite_e INTEGER DEFAULT 500,
            limite_f INTEGER DEFAULT 400,
            limite_g INTEGER DEFAULT 300,
            limite_h INTEGER DEFAULT 200,
            limite_i INTEGER DEFAULT 100,
            updated_at DATETIME,
            updated_by TEXT
        )
    """)
    print("[MIGRATION ETAPA 5] Tabela scoring_config OK")

    # Insere singleton se ainda não existir
    cur.execute("SELECT COUNT(*) FROM scoring_config WHERE id = 1")
    count = cur.fetchone()[0]
    if count == 0:
        campos = ", ".join(DEFAULTS.keys())
        placeholders = ", ".join(["?"] * len(DEFAULTS))
        cur.execute(
            f"INSERT INTO scoring_config (id, {campos}, updated_at, updated_by) "
            f"VALUES (1, {placeholders}, datetime('now'), 'migration')",
            tuple(DEFAULTS.values())
        )
        print("[MIGRATION ETAPA 5] Registro singleton inserido com defaults")
    else:
        print("[MIGRATION ETAPA 5] Singleton ja existe, mantendo valores atuais")

    conn.commit()
    conn.close()
    print("[MIGRATION ETAPA 5] OK")


if __name__ == "__main__":
    main()

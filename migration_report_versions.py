"""
migration_report_versions.py
Cria tabela report_versions e migra raw_json existente como versão 1.
Executar: python migration_report_versions.py
"""
import sqlite3, glob, os, json
from datetime import datetime


def encontrar_db():
    caminhos = ["/home/ubuntu/data/vibrascore.db", "vibrascore.db", "../vibrascore.db"]
    for p in caminhos:
        if os.path.exists(p): return p
    for padrao in ["*.db", "data/*.db"]:
        for f in glob.glob(padrao): return f
    return None


def main():
    db_path = encontrar_db()
    if not db_path:
        print("ERRO: .db não encontrado.")
        return
    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # criar tabela report_versions
    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_versions (
            id              TEXT PRIMARY KEY,
            analysis_id     TEXT NOT NULL,
            version_num     INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            created_by      TEXT,
            raw_json        TEXT,
            diff_json       TEXT,
            score_vibra     REAL,
            limite_recomendado REAL,
            doc_ids_usados  TEXT,
            notas           TEXT,
            UNIQUE(analysis_id, version_num)
        )
    """)
    print("[ok] tabela report_versions criada")

    # migrar raw_json existente como versão 1
    cur.execute("SELECT id, analysis_id, raw_json, created_at FROM reports WHERE raw_json IS NOT NULL AND raw_json != ''")
    reports = cur.fetchall()
    migrados = 0
    for report_id, analysis_id, raw_json, created_at in reports:
        # verificar se já existe versão 1
        cur.execute("SELECT id FROM report_versions WHERE analysis_id=? AND version_num=1", (analysis_id,))
        if cur.fetchone():
            continue
        # extrair score e limite do raw_json
        score = None
        limite = None
        try:
            raw = json.loads(raw_json)
            score = raw.get("scores", {}).get("vibra_composto")
            limite = raw.get("limite", {}).get("recomendado")
        except Exception:
            pass

        import uuid
        cur.execute("""
            INSERT INTO report_versions (id, analysis_id, version_num, created_at, raw_json, score_vibra, limite_recomendado)
            VALUES (?, ?, 1, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), analysis_id, created_at, raw_json, score, limite))
        migrados += 1

    # adicionar coluna version_atual em reports se não existe
    cur.execute("PRAGMA table_info(reports)")
    cols = [r[1] for r in cur.fetchall()]
    if "version_atual" not in cols:
        cur.execute("ALTER TABLE reports ADD COLUMN version_atual INTEGER DEFAULT 1")
        print("[add] reports.version_atual")
    if "faturamento_manual" not in cols:
        cur.execute("ALTER TABLE reports ADD COLUMN faturamento_manual TEXT")
        print("[add] reports.faturamento_manual")

    # adicionar em analyses também
    cur.execute("PRAGMA table_info(analyses)")
    cols = [r[1] for r in cur.fetchall()]
    if "faturamento_manual_json" not in cols:
        cur.execute("ALTER TABLE analyses ADD COLUMN faturamento_manual_json TEXT")
        print("[add] analyses.faturamento_manual_json")

    conn.commit()
    conn.close()
    print(f"[ok] {migrados} versão(ões) v1 criada(s)")
    print("Migration concluída.")


if __name__ == "__main__":
    main()

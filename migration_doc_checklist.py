"""
migration_doc_checklist.py
Cria tabela doc_checklist e popula com os documentos default.
Executar da pasta backend: python migration_doc_checklist.py
"""
import sqlite3, glob, json

DOCS_DEFAULT = [
    {"field_key": "bureau",      "label": "Bureau de Crédito (Vibra Full)", "required": "hard",       "formats": ".pdf",                    "ativo": 1, "ordem": 1},
    {"field_key": "scr",         "label": "SCR / BACEN",                    "required": "hard",       "formats": ".pdf",                    "ativo": 1, "ordem": 2},
    {"field_key": "faturamento", "label": "Declaração de Faturamento",      "required": "hard",       "formats": ".pdf,.xlsx",              "ativo": 1, "ordem": 3},
    {"field_key": "balanco",     "label": "Balanço Patrimonial ou Balancete","required": "obrigatorio","formats": ".pdf",                    "ativo": 1, "ordem": 4},
    {"field_key": "dre",         "label": "DRE — Demonstração do Resultado","required": "obrigatorio","formats": ".pdf",                    "ativo": 1, "ordem": 5},
    {"field_key": "endividamento","label": "Quadro de Endividamento",       "required": "obrigatorio","formats": ".pdf,.xlsx",              "ativo": 1, "ordem": 6},
    {"field_key": "irpf",        "label": "IRPF dos Sócios",                "required": "obrigatorio","formats": ".pdf",                    "ativo": 1, "ordem": 7},
    {"field_key": "contrato",    "label": "Contrato Social / Última Alteração","required": "obrigatorio","formats": ".pdf",                 "ativo": 1, "ordem": 8},
    {"field_key": "certidoes",   "label": "Certidões Negativas",            "required": "opcional",   "formats": ".pdf",                    "ativo": 1, "ordem": 9},
    {"field_key": "laudo",       "label": "Laudos de Avaliação",            "required": "opcional",   "formats": ".pdf",                    "ativo": 1, "ordem": 10},
    {"field_key": "curva",       "label": "Curva de Clientes / Carteira",   "required": "opcional",   "formats": ".pdf,.xlsx,.csv",         "ativo": 1, "ordem": 11},
    {"field_key": "outro_bureau_serasa",  "label": "Outro Bureau: Serasa Experian", "required": "opcional", "formats": ".pdf", "ativo": 1, "ordem": 12},
    {"field_key": "outro_bureau_boavista","label": "Outro Bureau: Boa Vista SCPC", "required": "opcional", "formats": ".pdf", "ativo": 1, "ordem": 13},
    {"field_key": "outro_bureau_spc",     "label": "Outro Bureau: SPC Brasil",     "required": "opcional", "formats": ".pdf", "ativo": 1, "ordem": 14},
    {"field_key": "outro_bureau_quod",    "label": "Outro Bureau: Quod",           "required": "opcional", "formats": ".pdf", "ativo": 1, "ordem": 15},
    {"field_key": "outro_bureau_outro",   "label": "Outro Bureau: Outro (não listado)", "required": "opcional", "formats": ".pdf", "ativo": 1, "ordem": 16},
]

def encontrar_db():
    for p in ["vibrascore.db","*.db","data/*.db","../*.db"]:
        for f in glob.glob(p):
            return f
    return None

def main():
    db_path = encontrar_db()
    if not db_path:
        print("ERRO: .db não encontrado.")
        return
    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS doc_checklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            field_key   TEXT NOT NULL UNIQUE,
            label       TEXT NOT NULL,
            required    TEXT DEFAULT 'opcional',
            formats     TEXT DEFAULT '.pdf',
            ativo       INTEGER DEFAULT 1,
            ordem       INTEGER DEFAULT 99
        )
    """)
    print("[ok] tabela doc_checklist criada/verificada")

    # inserir defaults solo si no existen
    inseridos = 0
    for d in DOCS_DEFAULT:
        cur.execute("SELECT id FROM doc_checklist WHERE field_key=?", (d["field_key"],))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO doc_checklist (field_key, label, required, formats, ativo, ordem)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (d["field_key"], d["label"], d["required"], d["formats"], d["ativo"], d["ordem"]))
            inseridos += 1

    conn.commit()
    conn.close()
    print(f"[ok] {inseridos} documento(s) inserido(s). Migration concluída.")

if __name__ == "__main__":
    main()

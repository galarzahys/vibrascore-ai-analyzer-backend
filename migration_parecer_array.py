"""
migration_parecer_array.py
Convierte parecer_analista_json de objeto único a array de pareceres.
Preserva datos existentes.

Ejecutar desde backend/:
    python migration_parecer_array.py
"""
import sqlite3, glob, json


def encontrar_db():
    for p in ["vibrascore.db", "*.db", "data/*.db", "../*.db"]:
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

    cur.execute("SELECT id, analysis_id, parecer_analista_json FROM reports WHERE parecer_analista_json IS NOT NULL AND parecer_analista_json != ''")
    rows = cur.fetchall()
    print(f"Reports com parecer: {len(rows)}")

    convertidos = 0
    for report_id, analysis_id, raw in rows:
        try:
            data = json.loads(raw)
        except Exception:
            continue

        # Si ya es array, no hacer nada
        if isinstance(data, list):
            continue

        # Es objeto único — convertir a array
        if isinstance(data, dict) and data:
            novo = [{
                "user_id": "legado",
                "email": "legado",
                "nome": data.get("nome_analista") or "Analista",
                "cargo": "",
                "parecer": data.get("parecer") or "",
                "limite": data.get("limite") or "",
                "limite_mem": data.get("limite_mem") or "",
                "notas_finais": data.get("notas_finais") or "",
                "pontos_fortes": data.get("pontos_fortes") or [],
                "pontos_atencao": data.get("pontos_atencao") or [],
                "condicionantes": data.get("condicionantes") or [],
                "ts": 0,
            }]
            cur.execute(
                "UPDATE reports SET parecer_analista_json=? WHERE id=?",
                (json.dumps(novo, ensure_ascii=False), report_id)
            )
            convertidos += 1

    conn.commit()
    conn.close()
    print(f"[ok] {convertidos} report(s) convertido(s) para array.")
    print("Migration concluída.")


if __name__ == "__main__":
    main()

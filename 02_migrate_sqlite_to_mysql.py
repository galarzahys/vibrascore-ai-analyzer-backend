"""
02_migrate_sqlite_to_mysql.py
Migra dados do SQLite para MySQL com conversão de client_id UUID → com_id INT.

Uso:
    pip install pymysql --break-system-packages
    python 02_migrate_sqlite_to_mysql.py

Configurar as variáveis abaixo antes de executar.
"""

import sqlite3
import pymysql
import json
import os

# ── CONFIGURAÇÃO ──────────────────────────────────────────────────
SQLITE_PATH = "/home/ubuntu/data/vibrascore.db"  # ajustar se necessário

MYSQL_CONFIG = {
    "host":     "credmaq.c1cgsky6amub.sa-east-1.rds.amazonaws.com",      # ajustar
    "port":     3306,
    "user":     "vibrascore_api",  # ajustar
    "password": "vibrascore_ax10m@",  # ajustar
    "database": "vibrascore",
    "charset":  "utf8mb4",
}

# Mapeo UUID → com_id (baseado no arquivo Excel fornecido)
# UUIDs marcados como 'excluir' → None (client_id NULL no MySQL)
UUID_TO_COM_ID = {
    "cliente-demo-0001":                    1,
    "03e459e1-d2fc-47b5-bce4-476aa1032158": 97,   # NOVA BITGOV
    "669e0704-5661-4861-a94f-c852395bb45e": 89,   # Capitale
    "c176da1e-8a6b-4eec-b064-7f308a3cba1e": 98,   # BZ AUTOMOTIVE
    "7ba12d33-85ad-406d-a4ab-122396f866bd": 106,  # QUARTZO CAPITAL
    "87ae45dc-3aba-4feb-9ee8-2c1688ce0619": 57,   # Extra Maquinas
    "ff33078f-f774-4808-bcf5-0716b2c19800": 108,  # AuthPay
    "0b9f0431-a925-4804-987a-a3d081ac5de1": 115,  # ACREDITAR
    "test123":                              107,  # ← dato de prueba
    # os seguintes são excluídos (client_id → NULL):
    "c4ae902f-2b0a-47e8-83e1-4bf93e77c6c8": None,
    "2f8f41d9-7a04-4853-919d-c6a22da5a870": None,
    "196cb3c6-101a-45bf-bce1-deb656f21957": None,
    "84e6494c-8cd4-40d5-a779-999db2d44c1b": None,
    "c4718d36-1bff-440e-8653-b70755103cbb": None,
}


def resolve_client_id(uuid_str):
    if not uuid_str:
        return None
    return UUID_TO_COM_ID.get(uuid_str, None)


def main():
    print(f"Conectando ao SQLite: {SQLITE_PATH}")
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row

    print(f"Conectando ao MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
    my = pymysql.connect(**MYSQL_CONFIG, autocommit=False)
    cur_sq = sq.cursor()
    cur_my = my.cursor()

    try:
        # ── 1. admin_config ──────────────────────────────────────
        print("\n[1/8] Migrando admin_config...")
        cur_sq.execute("SELECT * FROM admin_config")
        rows = cur_sq.fetchall()
        for r in rows:
            cur_my.execute("""
                INSERT IGNORE INTO vs_admin_config
                (id, plataforma_nome, score_min, admin_senha, defasagem_json, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (r["id"], r["plataforma_nome"], r["score_min"],
                  r["admin_senha"], r["defasagem_json"], r["updated_at"]))
        print(f"   {len(rows)} registro(s)")

        # ── 2. scoring_config ────────────────────────────────────
        print("[2/8] Migrando scoring_config...")
        cur_sq.execute("SELECT * FROM scoring_config")
        rows = cur_sq.fetchall()
        for r in rows:
            client_id = resolve_client_id(r["client_id"]) if r["client_id"] else None
            cur_my.execute("""
                INSERT IGNORE INTO vs_scoring_config
                (client_id, peso_bureau, peso_financeiro, peso_comportamental,
                 peso_cadastral, peso_tributario, peso_garantias, peso_cobertura,
                 limite_a, limite_b, limite_c, limite_d, limite_e,
                 limite_f, limite_g, limite_h, limite_i, updated_at, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (client_id, r["peso_bureau"], r["peso_financeiro"],
                  r["peso_comportamental"], r["peso_cadastral"], r["peso_tributario"],
                  r["peso_garantias"], r["peso_cobertura"],
                  r["limite_a"], r["limite_b"], r["limite_c"], r["limite_d"],
                  r["limite_e"], r["limite_f"], r["limite_g"], r["limite_h"],
                  r["limite_i"], r["updated_at"], r["updated_by"]))
        print(f"   {len(rows)} registro(s)")

        # ── 3. doc_checklist ─────────────────────────────────────
        print("[3/8] Migrando doc_checklist...")
        cur_sq.execute("SELECT * FROM doc_checklist ORDER BY ordem")
        rows = cur_sq.fetchall()
        for r in rows:
            cur_my.execute("""
                INSERT IGNORE INTO vs_doc_checklist
                (id, field_key, label, required, formats, ativo, ordem)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (r["id"], r["field_key"], r["label"],
                  r["required"], r["formats"], r["ativo"], r["ordem"]))
        print(f"   {len(rows)} registro(s)")

        # ── 4. api_integrations ──────────────────────────────────
        print("[4/8] Migrando api_integrations...")
        cur_sq.execute("SELECT * FROM api_integrations")
        rows = cur_sq.fetchall()
        migrados = 0
        for r in rows:
            client_id = resolve_client_id(r["client_id"]) if r["client_id"] else None
            try:
                cur_my.execute("""
                    INSERT IGNORE INTO vs_api_integrations
                    (client_id, api_key, api_secret, sandbox, ativo, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (client_id, r["api_key"], r["api_secret"],
                      r["sandbox"], r["ativo"], r["updated_at"]))
                migrados += 1
            except Exception as e:
                print(f"   AVISO api_integrations client_id={r['client_id']}: {e}")
        print(f"   {migrados}/{len(rows)} registro(s)")

        # ── 5. grupos ────────────────────────────────────────────
        print("[5/8] Migrando grupos...")
        cur_sq.execute("SELECT * FROM grupos")
        rows = cur_sq.fetchall()
        for r in rows:
            client_id = resolve_client_id(r["client_id"]) if r["client_id"] else None
            cur_my.execute("""
                INSERT IGNORE INTO vs_grupos
                (id, created_at, updated_at, nome, client_id, analista, diretrizes,
                 vibra_id, vibra_ver, consolidado_status, consolidado_raw_json,
                 parecer_consolidado, score_grupo, limite_consolidado,
                 limite_soma_individual, intercompany_obs)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (r["id"], r["created_at"], r["updated_at"], r["nome"],
                  client_id, r["analista"], r["diretrizes"],
                  r["vibra_id"], r["vibra_ver"], r["consolidado_status"],
                  r["consolidado_raw_json"], r["parecer_consolidado"],
                  r["score_grupo"], r["limite_consolidado"],
                  r["limite_soma_individual"], r["intercompany_obs"]))
        print(f"   {len(rows)} registro(s)")

        # ── 6. analyses ──────────────────────────────────────────
        print("[6/8] Migrando analyses...")
        cur_sq.execute("SELECT * FROM analyses")
        rows = cur_sq.fetchall()
        migrados = 0
        for r in rows:
            client_id = resolve_client_id(r["client_id"]) if r["client_id"] else None
            cur_my.execute("""
                INSERT IGNORE INTO vs_analyses
                (id, created_at, updated_at, status, company_name, cnpj,
                 analyst_name, package_level, historico_interno,
                 vibra_id, vibra_ver, client_id, grupo_id, ordem_no_grupo,
                 integration_bureau_id, integration_scr_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (r["id"], r["created_at"], r["updated_at"], r["status"],
                  r["company_name"], r["cnpj"], r["analyst_name"],
                  r["package_level"], r["historico_interno"],
                  r["vibra_id"], r["vibra_ver"], client_id,
                  r["grupo_id"], r["ordem_no_grupo"],
                  r["integration_bureau_id"] if "integration_bureau_id" in r.keys() else None,
                  r["integration_scr_id"] if "integration_scr_id" in r.keys() else None))
            migrados += 1
        print(f"   {migrados}/{len(rows)} registro(s)")

        # ── 7. documents ─────────────────────────────────────────
        print("[7/8] Migrando documents...")
        cur_sq.execute("SELECT * FROM documents")
        rows = cur_sq.fetchall()
        for r in rows:
            cur_my.execute("""
                INSERT IGNORE INTO vs_documents
                (id, analysis_id, created_at, field_key, field_label,
                 original_name, s3_key, file_size, mime_type,
                 is_valid, validation_msg, read_pct, doc_type_found, is_required)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (r["id"], r["analysis_id"], r["created_at"],
                  r["field_key"], r["field_label"], r["original_name"],
                  r["s3_key"], r["file_size"], r["mime_type"],
                  r["is_valid"], r["validation_msg"], r["read_pct"],
                  r["doc_type_found"], r["is_required"]))
        print(f"   {len(rows)} registro(s)")

        # ── 8. reports ───────────────────────────────────────────
        print("[8/8] Migrando reports...")
        cur_sq.execute("SELECT * FROM reports")
        rows = cur_sq.fetchall()
        cols = [d[0] for d in cur_sq.description]
        for r in rows:
            rd = dict(zip(cols, r))
            cur_my.execute("""
                INSERT IGNORE INTO vs_reports
                (id, analysis_id, created_at,
                 score_bureau, score_vibra, score_comportamental, score_financeiro,
                 score_cadastral, score_tributario, score_garantias, score_cobertura,
                 liquidez_corrente, liquidez_seca, endiv_pl, margem_liquida,
                 pmr_dias, pmp_dias, ciclo_financeiro, ncg, endiv_fat,
                 limite_recomendado, limite_calc_memo, parecer,
                 pontos_fortes, pontos_atencao, condicionantes,
                 qsa_analise, grupo_economico,
                 empresa_nome, empresa_cnpj, empresa_fantasia,
                 empresa_fundacao, empresa_regime, empresa_capital,
                 raw_json, overrides_json, parecer_analista_json,
                 comite_json, obs_json, feedback_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (rd.get("id"), rd.get("analysis_id"), rd.get("created_at"),
                  rd.get("score_bureau"), rd.get("score_vibra"),
                  rd.get("score_comportamental"), rd.get("score_financeiro"),
                  rd.get("score_cadastral"), rd.get("score_tributario"),
                  rd.get("score_garantias"), rd.get("score_cobertura"),
                  rd.get("liquidez_corrente"), rd.get("liquidez_seca"),
                  rd.get("endiv_pl"), rd.get("margem_liquida"),
                  rd.get("pmr_dias"), rd.get("pmp_dias"),
                  rd.get("ciclo_financeiro"), rd.get("ncg"), rd.get("endiv_fat"),
                  rd.get("limite_recomendado"), rd.get("limite_calc_memo"),
                  rd.get("parecer"), rd.get("pontos_fortes"),
                  rd.get("pontos_atencao"), rd.get("condicionantes"),
                  rd.get("qsa_analise"), rd.get("grupo_economico"),
                  rd.get("empresa_nome"), rd.get("empresa_cnpj"),
                  rd.get("empresa_fantasia"), rd.get("empresa_fundacao"),
                  rd.get("empresa_regime"), rd.get("empresa_capital"),
                  rd.get("raw_json"), rd.get("overrides_json"),
                  rd.get("parecer_analista_json"), rd.get("comite_json"),
                  rd.get("obs_json"), rd.get("feedback_json")))
        print(f"   {len(rows)} registro(s)")

        my.commit()
        print("\n✅ Migração concluída com sucesso!")

    except Exception as e:
        my.rollback()
        print(f"\n❌ ERRO — rollback executado: {e}")
        raise
    finally:
        sq.close()
        my.close()


if __name__ == "__main__":
    main()

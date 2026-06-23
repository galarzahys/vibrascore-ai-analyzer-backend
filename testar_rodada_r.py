"""
testar_rodada_r.py — Testa a consolidação IA do grupo mais recente.

Uso (na pasta backend, com o uvicorn rodando em outro terminal):

    python testar_rodada_r.py

O script:
  1) Lista os grupos do banco.
  2) Pega o mais recente.
  3) Dispara POST /api/grupos/{id}/consolidate.
  4) Faz polling do status a cada 5s até concluir (ou dar erro / timeout).
  5) Imprime o resultado completo.
"""

import urllib.request
import urllib.error
import json
import sys
import time

API = "http://127.0.0.1:8000/api"


def http_get(path: str):
    req = urllib.request.Request(API + path)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def http_post(path: str):
    req = urllib.request.Request(API + path, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body.decode("utf-8", errors="ignore")}


def fmt_money(v):
    if v is None:
        return "—"
    try:
        s = f"R$ {float(v):,.2f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def main():
    print("=" * 70)
    print("TESTE — Rodada R.1 (consolidação IA do grupo)")
    print("=" * 70)

    # ── 1) Listar grupos ──
    try:
        grupos = http_get("/grupos/list-by-client/all")
    except Exception as e:
        print(f"\n[ERRO] Backend não respondeu em {API}.")
        print(f"   Detalhe: {e}")
        print("   Confirme que o uvicorn está rodando.")
        sys.exit(1)

    if not grupos:
        print("\nNenhum grupo encontrado no banco. Crie um grupo pela UI primeiro.")
        sys.exit(1)

    print(f"\n{len(grupos)} grupo(s) no banco. 5 mais recentes:")
    for i, g in enumerate(grupos[:5]):
        marca = " <-- vai testar este" if i == 0 else ""
        print(f"  [{i+1}] id={g['id']}")
        print(f"      nome={g.get('nome')!r} ({g.get('qtd_empresas',0)} empresas) status_cons={g.get('consolidado_status','-')}{marca}")

    grupo_id = grupos[0]["id"]
    nome = grupos[0].get("nome")
    print(f"\nUsando: {grupo_id} ({nome!r})")

    # ── 2) Disparar /consolidate ──
    print(f"\nPOST /api/grupos/{grupo_id}/consolidate ...")
    status, body = http_post(f"/grupos/{grupo_id}/consolidate")
    print(f"   HTTP {status}: {body}")

    if status != 200:
        print("\n[ATENÇÃO] Não foi possível iniciar a consolidação.")
        if status == 400 and "Mínimo 2" in str(body):
            print("   Causa: este grupo não tem pelo menos 2 análises filhas em status 'done'.")
            print("   Solução: use um grupo onde 2+ empresas tenham análise concluída.")
        sys.exit(1)

    # ── 3) Polling ──
    print(f"\nAguardando consolidação. (Veja [GRUPO-CONS ...] no terminal do uvicorn)")
    print("   Polling a cada 5s (máx 3min).")
    print()

    for tentativa in range(36):   # 36 * 5s = 180s = 3min
        time.sleep(5)
        try:
            data = http_get(f"/grupos/{grupo_id}/consolidate-status")
        except Exception as e:
            print(f"   [{(tentativa+1)*5:>3}s] erro ao consultar status: {e}")
            continue

        st = data.get("status", "?")
        print(f"   [{(tentativa+1)*5:>3}s] status={st}", end="")
        if data.get("score_grupo"):
            print(f"  score={data['score_grupo']}", end="")
        print()

        if st == "done":
            print("\n" + "=" * 70)
            print("✓ CONSOLIDAÇÃO CONCLUÍDA")
            print("=" * 70)
            print(f"Score do grupo:           {data.get('score_grupo')}")
            print(f"Limite consolidado:       {fmt_money(data.get('limite_consolidado'))}")
            print(f"Limite (soma individual): {fmt_money(data.get('limite_soma_individual'))}")

            cons = data.get("consolidado") or {}
            if cons:
                print(f"Classe:                   {cons.get('score_classe', '—')}")
                print(f"% Redução vs soma:        {cons.get('limite_pct_reducao', '—')}%")
                print()
                fin = cons.get("consolidado_financeiro", {})
                print("Consolidado financeiro:")
                print(f"  Fat. anual (soma):       {fmt_money(fin.get('faturamento_anual_soma_simples'))}")
                print(f"  Fat. anual (consol.):    {fmt_money(fin.get('faturamento_anual_consolidado'))}")
                print(f"  Endividamento total:     {fmt_money(fin.get('endividamento_total_consolidado'))}")
                print(f"  EBITDA consolidado:      {fmt_money(fin.get('ebitda_consolidado'))}")
                print(f"  Liquidez ponderada:      {fin.get('liquidez_media_ponderada', '—')}")

                inter = cons.get("intercompany", {})
                print()
                print(f"Intercompany detectado:    {inter.get('detectado')}")
                if inter.get("valor_estimado_anual"):
                    print(f"  Valor estimado anual:    {fmt_money(inter.get('valor_estimado_anual'))}")
                if inter.get("observacoes"):
                    print(f"  Observações:             {inter['observacoes'][:200]}")

                pf = cons.get("pontos_fortes_grupo", [])
                pa = cons.get("pontos_atencao_grupo", [])
                print()
                print(f"Pontos Fortes ({len(pf)}):")
                for p in pf[:5]:
                    if isinstance(p, dict):
                        print(f"  - {p.get('titulo', '')}: {p.get('descricao', '')[:100]}")
                print(f"Pontos de Atenção ({len(pa)}):")
                for p in pa[:5]:
                    if isinstance(p, dict):
                        nivel = p.get("nivel", "?")
                        print(f"  - [{nivel}] {p.get('titulo', '')}: {p.get('descricao', '')[:100]}")

                rec = cons.get("recomendacao", {})
                print()
                print(f"Recomendação: {rec.get('decisao', '—')}")
                if rec.get("condicionantes"):
                    print(f"  Condicionantes ({len(rec['condicionantes'])}):")
                    for c in rec["condicionantes"][:5]:
                        print(f"    - {c}")
                if rec.get("observacoes_finais"):
                    print(f"  Observações: {rec['observacoes_finais'][:200]}")

            print()
            print("Parecer Consolidado (primeiras 800 chars):")
            print("-" * 70)
            print((data.get("parecer_consolidado") or "")[:800])
            print("-" * 70)
            print()
            return

        if st == "error":
            print("\n" + "=" * 70)
            print("✗ CONSOLIDAÇÃO FALHOU")
            print("=" * 70)
            print(f"Mensagem: {data.get('parecer_consolidado')}")
            print("Veja o stack completo no terminal do uvicorn.")
            return

    print("\n[TIMEOUT] consolidação ainda em processo após 3 minutos.")
    print("Veja [GRUPO-CONS ...] no terminal do uvicorn para saber em que etapa está.")


if __name__ == "__main__":
    main()

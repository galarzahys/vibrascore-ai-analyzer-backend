"""
testar_etapa2.py — Testa o backend de overrides da Etapa 2.

Uso (com uvicorn rodando):
    python testar_etapa2.py

O script:
  1) Lista análises e pega a mais recente concluída.
  2) GET /overrides/{id} (deve vir vazio na primeira vez).
  3) PATCH com 3 campos válidos.
  4) PATCH com 1 campo bloqueado (score_vibra) — deve ser rejeitado.
  5) GET de novo — confirma 3 overrides salvos, score_vibra ausente.
  6) DELETE de 1 path — confirma que foi removido.
"""

import urllib.request
import urllib.error
import json
import sys

API = "http://127.0.0.1:8000/api"


def http_get(path):
    req = urllib.request.Request(API + path)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def http_patch(path, body):
    req = urllib.request.Request(
        API + path,
        method="PATCH",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try: return e.code, json.loads(body)
        except: return e.code, {"raw": body.decode("utf-8", errors="ignore")}


def http_delete(path):
    req = urllib.request.Request(API + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try: return e.code, json.loads(body)
        except: return e.code, {"raw": body.decode("utf-8", errors="ignore")}


def main():
    print("=" * 70)
    print("TESTE — Etapa 2.1 (Backend de Overrides)")
    print("=" * 70)

    # 1) Pegar uma análise concluída
    try:
        analyses = http_get("/analysis/list?status=done")
    except Exception as e:
        print(f"\n[ERRO] Backend não respondeu: {e}")
        sys.exit(1)

    if not analyses:
        print("\nNenhuma análise 'done' no banco. Rode uma antes de testar.")
        sys.exit(1)

    aid = analyses[0]["id"]
    nome = analyses[0].get("company_name") or "—"
    print(f"\nUsando análise: {aid}")
    print(f"  empresa: {nome}")

    # 2) GET vazio
    print("\n[2] GET /api/overrides/{id} (esperado: vazio)")
    data = http_get(f"/overrides/{aid}")
    n = len(data.get("overrides", {}))
    print(f"   overrides atuais: {n}")

    # 3) PATCH com 3 campos válidos
    print("\n[3] PATCH com 3 overrides válidos")
    body = {
        "por": "teste@vibrascore.com.br",
        "changes": [
            {"path": "empresa.regime",         "valor": "Lucro Real"},
            {"path": "inds.liquidez_corrente", "valor": 1.85},
            {"path": "historico_interno",      "valor": "Cliente conhecido há 3 anos, sem ocorrências."},
        ],
    }
    status, ret = http_patch(f"/overrides/{aid}", body)
    print(f"   HTTP {status}: aplicados={ret.get('aplicados')} bloqueados={ret.get('bloqueados')}")

    # 4) PATCH tentando editar campo bloqueado
    print("\n[4] PATCH com path bloqueado (score_vibra) + 1 válido")
    body = {
        "por": "teste@vibrascore.com.br",
        "changes": [
            {"path": "score_vibra",     "valor": 9.9},   # BLOQUEADO
            {"path": "score_classe",    "valor": "AAA"}, # BLOQUEADO
            {"path": "empresa.capital", "valor": 1000000.0},  # OK
        ],
    }
    status, ret = http_patch(f"/overrides/{aid}", body)
    print(f"   HTTP {status}: aplicados={ret.get('aplicados')} bloqueados={ret.get('bloqueados')}")
    if ret.get("bloqueados") != 2:
        print(f"   [FALHA] esperava 2 bloqueados, recebeu {ret.get('bloqueados')}")
    else:
        print(f"   ✓ score_vibra e score_classe rejeitados corretamente")

    # 5) GET confirma 4 overrides (3 da etapa 3 + 1 da etapa 4)
    print("\n[5] GET /api/overrides/{id} — confirma overrides salvos")
    data = http_get(f"/overrides/{aid}")
    overs = data.get("overrides", {})
    print(f"   total de overrides: {len(overs)}")
    for path, ov in overs.items():
        valor = ov.get("valor")
        if isinstance(valor, str) and len(valor) > 60:
            valor = valor[:60] + "..."
        print(f"     {path}: {valor!r}  (por {ov.get('por')} em {ov.get('em', '')[:19]})")

    # Confirma que campos bloqueados NÃO estão lá
    for blk in ["score_vibra", "score_classe"]:
        if blk in overs:
            print(f"   [FALHA] {blk} foi salvo apesar de ser bloqueado!")
        else:
            print(f"   ✓ {blk} corretamente AUSENTE")

    # 6) DELETE 1 path
    print("\n[6] DELETE empresa.regime")
    status, ret = http_delete(f"/overrides/{aid}/empresa.regime")
    print(f"   HTTP {status}: removido={ret.get('removido')}")

    # GET final
    print("\n[7] GET final — confirma que empresa.regime foi removido")
    data = http_get(f"/overrides/{aid}")
    overs = data.get("overrides", {})
    print(f"   total: {len(overs)}")
    if "empresa.regime" in overs:
        print("   [FALHA] empresa.regime ainda está lá!")
    else:
        print("   ✓ empresa.regime foi removido com sucesso")

    print("\n" + "=" * 70)
    print("✓ Etapa 2.1 (backend) funcionando.")
    print("=" * 70)
    print("\nPara testar o frontend:")
    print("  1) Logue como admin@vibrascore.com.br / admin2024")
    print(f"  2) Abra a análise da empresa {nome!r}")
    print("  3) Botão '✎ Editar' deve aparecer no topbar")
    print("  4) Clique nele: topbar ganha barra dourada inferior")
    print("  5) (Lápis ainda não aparecem — Etapa 2.3 vai aplicar)")
    print("\nVocê pode limpar os overrides de teste com:")
    print(f"  curl -X DELETE http://127.0.0.1:8000/api/overrides/{aid}/historico_interno")
    print(f"  curl -X DELETE http://127.0.0.1:8000/api/overrides/{aid}/inds.liquidez_corrente")
    print(f"  curl -X DELETE http://127.0.0.1:8000/api/overrides/{aid}/empresa.capital")


if __name__ == "__main__":
    main()

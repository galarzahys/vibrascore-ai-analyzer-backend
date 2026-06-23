"""
Vibra Score — Serviço de Consolidação de Grupo Econômico (Rodada R)

Recebe os Reports individuais das empresas filhas que já concluíram análise
e produz um relatório consolidado do GRUPO como entidade única, considerando:
- Soma simples + ajuste de operações intercompany
- Score consolidado próprio (não média)
- Limite consolidado próprio com comparativo soma vs consolidado
- Análise de intercompany pela IA
- Pontos fortes/atenção do grupo

Sem modificação no analysis_service.py (motor singular intocado).
Reusa estratégia de retry/backoff da Rodada Q para lidar com falhas do streaming Anthropic.
"""

import os
import json
import time
import random
import traceback
from dotenv import load_dotenv
import anthropic

from models.database import SessionLocal, Grupo, Analysis, Report

# Carrega .env do diretório do backend
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY não encontrada. "
            "Crie o arquivo backend/.env com ANTHROPIC_API_KEY=sua-chave."
        )
    return anthropic.Anthropic(api_key=api_key)


def _fmt_money(v) -> str:
    if v is None:
        return "—"
    try:
        s = f"R$ {float(v):,.2f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def _fmt_num(v, dec: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{dec}f}"
    except Exception:
        return "—"


def _safe_text(v, limite: int = 1500) -> str:
    if not v:
        return "—"
    s = str(v).strip()
    return s[:limite] + ("…" if len(s) > limite else "")


def _safe_json_list(v):
    if not v:
        return []
    try:
        parsed = json.loads(v) if isinstance(v, str) else v
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _montar_resumo_filha(analysis: Analysis, report: Report) -> str:
    """Monta resumo estruturado de uma empresa filha para alimentar o prompt."""

    nome = report.empresa_nome or analysis.company_name or "Empresa sem nome"
    cnpj = report.empresa_cnpj or analysis.cnpj or "CNPJ não identificado"
    regime = report.empresa_regime or "—"
    capital = _fmt_money(report.empresa_capital)

    score = _fmt_num(report.score_vibra, 1)
    limite = _fmt_money(report.limite_recomendado)
    limite_memo = _safe_text(report.limite_calc_memo, 600)

    lc = _fmt_num(report.liquidez_corrente)
    ls = _fmt_num(report.liquidez_seca)
    epl = _fmt_num(report.endiv_pl)
    efat = _fmt_num(report.endiv_fat)
    ml = _fmt_num(report.margem_liquida)
    pmr = _fmt_num(report.pmr_dias, 0)
    pmp = _fmt_num(report.pmp_dias, 0)
    ciclo = _fmt_num(report.ciclo_financeiro, 0)
    ncg = _fmt_money(report.ncg)

    parecer = _safe_text(report.parecer, 1500)

    pf = _safe_json_list(report.pontos_fortes)
    pa = _safe_json_list(report.pontos_atencao)

    def _formatar_pontos(lista, limit=8):
        if not lista:
            return "  (nenhum identificado)"
        out = []
        for p in lista[:limit]:
            if isinstance(p, dict):
                titulo = p.get("titulo", p.get("title", ""))
                desc = p.get("descricao", p.get("description", p.get("desc", "")))
                nivel = p.get("nivel", p.get("severity", ""))
                nivel_str = f"[{nivel}] " if nivel else ""
                out.append(f"  - {nivel_str}{titulo}: {desc}")
            else:
                out.append(f"  - {p}")
        return "\n".join(out)

    pf_str = _formatar_pontos(pf)
    pa_str = _formatar_pontos(pa)

    return f"""Empresa: {nome}
CNPJ: {cnpj}
Regime tributário: {regime}
Capital social: {capital}

Score Vibra individual: {score}
Limite individual recomendado: {limite}
Memória de cálculo do limite individual:
{limite_memo}

Indicadores financeiros (período principal):
- Liquidez Corrente: {lc}
- Liquidez Seca: {ls}
- Endividamento / PL: {epl}
- Endividamento / Faturamento (meses): {efat}
- Margem Líquida: {ml}
- PMR: {pmr} dias
- PMP: {pmp} dias
- Ciclo Financeiro: {ciclo} dias
- NCG: {ncg}

Parecer individual:
{parecer}

Pontos Fortes individuais:
{pf_str}

Pontos de Atenção individuais:
{pa_str}"""


def _montar_prompt_consolidacao(grupo: Grupo, resumos_filhas: list) -> str:
    """Monta o prompt completo de consolidação."""

    nome_grupo = grupo.nome or "Grupo sem nome"
    diretrizes = grupo.diretrizes or "Sem diretrizes específicas declaradas pelo analista."
    intercompany_obs = grupo.intercompany_obs or "Não declarado pelo analista."

    empresas_str = "\n\n".join([
        f"═══════════════ EMPRESA {i+1} ═══════════════\n{resumo}"
        for i, resumo in enumerate(resumos_filhas)
    ])

    return f"""Você é um especialista em análise de crédito para grupos econômicos brasileiros. Você atua para uma empresa que concede crédito a outras empresas (factoring, FIDC, securitizadora, distribuidor que vende a prazo, etc).

GRUPO ANALISADO: {nome_grupo}
NÚMERO DE EMPRESAS NO GRUPO: {len(resumos_filhas)}

DIRETRIZES DO ANALISTA RESPONSÁVEL PELO GRUPO:
{diretrizes}

OBSERVAÇÕES SOBRE OPERAÇÕES INTERCOMPANY DECLARADAS PELO ANALISTA:
{intercompany_obs}

═════════════════════════════════════════
DADOS DAS EMPRESAS DO GRUPO:
═════════════════════════════════════════

{empresas_str}

═════════════════════════════════════════
SUA TAREFA:
═════════════════════════════════════════

Analise o GRUPO como entidade única (não como soma de partes) e produza UM relatório consolidado considerando:

1. SOMA SIMPLES dos faturamentos individuais como ponto de partida.
2. AJUSTE de operações intercompany detectadas nos dados ou declaradas pelo analista, para evitar dupla contagem.
3. SCORE consolidado próprio: é a SUA avaliação holística do grupo, NÃO a média dos scores individuais.
4. LIMITE consolidado próprio: refletindo que a contraparte real é o GRUPO. Geralmente é menor que a soma dos limites individuais por concentração de risco.
5. Análise de pontos fortes e pontos de atenção do GRUPO.
6. Recomendação única para o grupo todo.

REGRAS RÍGIDAS:
- Não invente dados que não foram fornecidos.
- Se algum dado essencial estiver faltando, registre como ponto de atenção.
- Se detectar intercompany nos dados (mesmo sem declaração do analista), explicite na seção "intercompany".
- Score do grupo nunca pode ser MAIOR que (maior score individual + 1.0) nem MENOR que (menor score individual - 1.0).
- Limite consolidado deve estar entre 60% e 100% da soma dos limites individuais.
- Liquidez média ponderada: pondere pelo faturamento individual de cada empresa.

═════════════════════════════════════════
FORMATO DE RESPOSTA:
═════════════════════════════════════════

Responda APENAS com um JSON puro. Sem ```json envolvendo, sem markdown, sem texto antes ou depois.

Schema:

{{
  "score_grupo": <float entre 0 e 10>,
  "score_classe": "<AAA, AA, A, BBB, BB, B, CCC, CC, C ou D>",
  "limite_consolidado": <float em reais>,
  "limite_soma_individual": <float - soma dos limites individuais>,
  "limite_pct_reducao": <float - % de redução do consolidado vs soma simples, ex: 22.5>,
  "memoria_calculo_limite": "<2 a 4 linhas explicando como chegou no limite consolidado>",

  "parecer_consolidado": "<texto narrativo de 3 a 5 parágrafos tratando o grupo como entidade única; contexto, capacidade de pagamento consolidada, governança, riscos e oportunidades>",

  "intercompany": {{
    "detectado": <true ou false>,
    "valor_estimado_anual": <float em reais ou null>,
    "observacoes": "<o que foi detectado e como o ajuste afetou o consolidado>"
  }},

  "consolidado_financeiro": {{
    "faturamento_anual_soma_simples": <float - soma sem ajuste>,
    "faturamento_anual_consolidado": <float - depois do ajuste intercompany>,
    "endividamento_total_consolidado": <float>,
    "ebitda_consolidado": <float ou null>,
    "liquidez_media_ponderada": <float - ponderada pelo faturamento de cada empresa>,
    "endiv_x_fat_meses_consolidado": <float>
  }},

  "pontos_fortes_grupo": [
    {{"titulo": "<curto>", "descricao": "<1 a 2 linhas>"}}
  ],

  "pontos_atencao_grupo": [
    {{"nivel": "<alto, medio ou baixo>", "titulo": "<curto>", "descricao": "<1 a 2 linhas>"}}
  ],

  "recomendacao": {{
    "decisao": "<aprovar, aprovar com condicoes ou negar>",
    "condicionantes": ["<condição 1>", "<condição 2>"],
    "observacoes_finais": "<texto curto fechando>"
  }}
}}
"""


def _chamar_claude_consolidacao(prompt: str, tag: str) -> dict:
    """
    Chama Claude com streaming + retry/backoff (mesma estratégia da Rodada Q).
    Retorna o JSON parseado.
    """
    client = _get_client()

    MAX_TENTATIVAS = 3
    ERROS_TRANSITORIOS = (
        "RemoteProtocolError",
        "incomplete chunked read",
        "ConnectionError",
        "ReadTimeout",
        "PoolTimeout",
        "ConnectTimeout",
        "rate_limit",
        "overloaded",
    )

    ultimo_erro = None

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            print(f"{tag}   chamando Claude (tentativa {tentativa}/{MAX_TENTATIVAS})...", flush=True)

            texto_completo = ""
            with client.messages.stream(
                model="claude-sonnet-4-5",
                max_tokens=24000,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    texto_completo += text

            print(f"{tag}   resposta recebida: {len(texto_completo)} chars", flush=True)

            # Parse robusto: remove eventual cerca markdown se a IA insistir
            raw = texto_completo.strip()
            if raw.startswith("```"):
                partes = raw.split("```", 2)
                if len(partes) >= 2:
                    raw = partes[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                else:
                    raw = ""
                raw = raw.strip()
            if raw.endswith("```"):
                raw = raw[:-3].strip()

            result = json.loads(raw)
            print(f"{tag}   ✓ JSON parseado com sucesso", flush=True)
            return result

        except Exception as e:
            err_name = type(e).__name__
            err_msg = str(e)[:200]
            print(f"{tag}   ✗ erro tentativa {tentativa}: {err_name}: {err_msg}", flush=True)
            ultimo_erro = e

            transitorio = any(t in err_name or t in err_msg for t in ERROS_TRANSITORIOS)
            if tentativa < MAX_TENTATIVAS and transitorio:
                backoff = 5 * tentativa + random.uniform(0, 2)
                print(f"{tag}   erro transitório, aguardando {backoff:.1f}s antes de retry...", flush=True)
                time.sleep(backoff)
            else:
                if not transitorio:
                    print(f"{tag}   erro NÃO transitório — não vai retentar", flush=True)
                else:
                    print(f"{tag}   esgotou tentativas", flush=True)
                raise ultimo_erro

    raise ultimo_erro or RuntimeError("Falha após todas as tentativas")


def run_consolidated_analysis(grupo_id: str) -> dict:
    """
    Orquestra a consolidação de um grupo:
    1. Carrega o grupo e suas filhas com status='done'
    2. Para cada filha, monta resumo estruturado a partir do Report
    3. Constrói o prompt de consolidação
    4. Chama Claude com retry/backoff
    5. Persiste resultado em grupos.consolidado_raw_json + campos diretos

    Levanta ValueError se menos de 2 filhas estão em 'done'.
    Atualiza grupo.consolidado_status: pending → processing → done | error.
    """
    tag = f"[GRUPO-CONS {grupo_id[:8]}]"
    print(f"\n{tag} === Iniciando consolidação ===", flush=True)

    db = SessionLocal()
    try:
        grupo = db.query(Grupo).filter(Grupo.id == grupo_id).first()
        if not grupo:
            raise ValueError(f"Grupo {grupo_id} não encontrado")

        grupo.consolidado_status = "processing"
        db.commit()

        # Filhas em 'done'
        filhas_done = (
            db.query(Analysis)
            .filter(Analysis.grupo_id == grupo_id, Analysis.status == "done")
            .order_by(Analysis.ordem_no_grupo.asc())
            .all()
        )

        print(f"{tag} filhas em 'done': {len(filhas_done)}", flush=True)

        if len(filhas_done) < 2:
            grupo.consolidado_status = "error"
            grupo.parecer_consolidado = (
                f"Consolidação não pôde ser executada: apenas {len(filhas_done)} empresa(s) "
                f"do grupo está(ão) em status 'done'. É necessário pelo menos 2 análises "
                f"concluídas para consolidar."
            )
            db.commit()
            raise ValueError(f"Apenas {len(filhas_done)} filhas em 'done'. Mínimo 2.")

        # Monta resumos
        resumos = []
        for filha in filhas_done:
            report = db.query(Report).filter(Report.analysis_id == filha.id).first()
            if not report:
                print(f"{tag}   filha {filha.id[:8]} sem report — pulando", flush=True)
                continue
            resumo = _montar_resumo_filha(filha, report)
            resumos.append(resumo)
            print(f"{tag}   resumo da filha {filha.id[:8]} montado ({len(resumo)} chars)", flush=True)

        if len(resumos) < 2:
            grupo.consolidado_status = "error"
            grupo.parecer_consolidado = f"Apenas {len(resumos)} resumos válidos. Mínimo 2 para consolidar."
            db.commit()
            raise ValueError(f"Apenas {len(resumos)} resumos válidos.")

        # Monta prompt
        prompt = _montar_prompt_consolidacao(grupo, resumos)
        print(f"{tag} prompt montado: {len(prompt)} chars", flush=True)

        # Chama Claude com retry
        try:
            result = _chamar_claude_consolidacao(prompt, tag)
        except Exception as e:
            grupo.consolidado_status = "error"
            grupo.parecer_consolidado = f"Erro na consolidação IA: {type(e).__name__}: {str(e)[:300]}"
            db.commit()
            raise

        # Persiste resultado
        grupo.consolidado_raw_json = json.dumps(result, ensure_ascii=False)
        grupo.parecer_consolidado = result.get("parecer_consolidado", "")
        try:
            grupo.score_grupo = float(result.get("score_grupo") or 0)
        except (TypeError, ValueError):
            grupo.score_grupo = None
        try:
            grupo.limite_consolidado = float(result.get("limite_consolidado") or 0)
        except (TypeError, ValueError):
            grupo.limite_consolidado = None
        try:
            grupo.limite_soma_individual = float(result.get("limite_soma_individual") or 0)
        except (TypeError, ValueError):
            grupo.limite_soma_individual = None
        grupo.consolidado_status = "done"
        db.commit()

        print(f"{tag} ✓ consolidação concluída e persistida", flush=True)
        print(f"{tag}   score_grupo={grupo.score_grupo} limite={grupo.limite_consolidado}", flush=True)
        print(f"{tag} === Fim da consolidação ===\n", flush=True)

        return result

    except Exception:
        traceback.print_exc()
        raise
    finally:
        db.close()

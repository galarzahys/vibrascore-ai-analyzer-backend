"""
Vibra Score — Servico de analise de credito via Claude API
Suporte a múltiplos arquivos por campo (até 5)
"""

import anthropic
import json
import os
import re
import pdfplumber
import io
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY nao encontrada.")
    return anthropic.Anthropic(api_key=api_key)


def extract_text_from_bytes(file_bytes: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            texts = []
            for page in pdf.pages:
                text = page.extract_text()
                if text and len(text.strip()) > 10:
                    texts.append(text)
            return "\n\n".join(texts)
    except Exception:
        return ""


def _truncate_smart(text: str, field_key: str) -> str:
    """Bureau sem truncamento. Demais documentos: até 12.000 chars."""
    is_bureau = any(kw in field_key.lower() for kw in ("bureau", "vibra", "consulta", "score"))
    if is_bureau:
        return text
    return text[:12000]


def _find_local_files(upload_dir: str, field_key: str, doc) -> list:
    """Encontra todos os arquivos locais para um campo (suporte a múltiplos)."""
    paths = []
    
    # Novo formato: field_key_N_filename (múltiplos)
    if doc.s3_key and doc.s3_key.startswith("local:"):
        safe_name = doc.s3_key[6:]
        local_path = os.path.join(upload_dir, safe_name)
        if os.path.exists(local_path):
            paths.append(local_path)
            return paths
    
    # Formato antigo: field_key_filename (compatibilidade)
    old_path = os.path.join(upload_dir, field_key + "_" + doc.original_name)
    if os.path.exists(old_path):
        paths.append(old_path)
        return paths
    
    # Buscar por padrão no diretório
    if os.path.exists(upload_dir):
        for fname in sorted(os.listdir(upload_dir)):
            if fname.startswith(field_key + "_"):
                paths.append(os.path.join(upload_dir, fname))
    
    return paths


def run_full_analysis(analysis_id: str, documents: list, db, diretrizes: str = "") -> dict:
    from models.database import Report, Analysis

    db_analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    db_analysis.status = "processing"
    db.commit()

    # ===== ETAPA 3 INICIO — Limite de 20 docs para análise =====
    MAX_DOCS_ANALISAVEIS = 20
    total_recebidos = len(documents)
    docs_excedentes = []
    if total_recebidos > MAX_DOCS_ANALISAVEIS:
        # Mantém a ordem original (priorizar por ordem de upload); restantes ficam de fora
        docs_excedentes = documents[MAX_DOCS_ANALISAVEIS:]
        documents = documents[:MAX_DOCS_ANALISAVEIS]
        print(f"[ANALYSIS {analysis_id[:8]}] Limite atingido: {total_recebidos} docs recebidos, "
              f"analisando os {MAX_DOCS_ANALISAVEIS} primeiros. "
              f"{len(docs_excedentes)} ficaram anexados como referência.", flush=True)
    else:
        print(f"[ANALYSIS {analysis_id[:8]}] Iniciando análise com {total_recebidos} documento(s).", flush=True)
    # ===== ETAPA 3 FIM =====

    try:
        # Agrupar documentos por campo
        docs_by_field = {}
        for doc in documents:
            if doc.field_key not in docs_by_field:
                docs_by_field[doc.field_key] = []
            docs_by_field[doc.field_key].append(doc)

        doc_texts = {}
        upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads", analysis_id)

        for field_key, field_docs in docs_by_field.items():
            field_texts = []
            
            for i, doc in enumerate(field_docs[:5]):  # máximo 5 por campo
                try:
                    # Encontrar arquivo local
                    local_paths = _find_local_files(upload_dir, field_key, doc)
                    
                    text_extracted = False
                    for local_path in local_paths:
                        if os.path.exists(local_path):
                            with open(local_path, "rb") as f:
                                file_bytes = f.read()
                            text = extract_text_from_bytes(file_bytes)
                            if text:
                                label = f"Arquivo {i+1}: {doc.original_name}"
                                field_texts.append(f"[{label}]\n{_truncate_smart(text, field_key)}")
                                text_extracted = True
                                break
                    
                    # Fallback S3
                    if not text_extracted and doc.s3_key and not doc.s3_key.startswith("local:"):
                        try:
                            from services.s3_service import download_file
                            file_bytes = download_file(doc.s3_key)
                            text = extract_text_from_bytes(file_bytes)
                            if text:
                                field_texts.append(f"[Arquivo {i+1}: {doc.original_name}]\n{_truncate_smart(text, field_key)}")
                        except Exception:
                            pass

                except Exception:
                    continue

            if field_texts:
                # Múltiplos arquivos do mesmo campo concatenados
                if len(field_texts) > 1:
                    doc_texts[field_key] = f"[{len(field_texts)} arquivo(s) enviados para este campo]\n\n" + "\n\n---\n\n".join(field_texts)
                else:
                    doc_texts[field_key] = field_texts[0]

        if not doc_texts:
            doc_info = "\n".join([
                f"- {d.field_label or d.field_key}: {d.original_name}"
                for d in documents if d.is_valid
            ])
            doc_texts["metadados"] = f"Documentos validados:\n{doc_info}"

        # Incluir histórico interno
        historico_interno = getattr(db_analysis, 'historico_interno', None) or ''
        if historico_interno:
            doc_texts['historico_interno'] = f"HISTORICO DE RELACIONAMENTO INTERNO:\n{historico_interno}"

        # ===== ETAPA 4 INICIO — Priorização de bureaus =====
        # Vibra Full ('bureau') é o primário se presente; senão, primeiro outro_bureau_* assume.
        # Demais bureaus são SECUNDÁRIOS (referência adicional).
        BUREAU_LABELS = {
            'bureau':               'Vibra Full',
            'outro_bureau_serasa':  'Serasa Experian',
            'outro_bureau_boavista':'Boa Vista SCPC',
            'outro_bureau_spc':     'SPC Brasil',
            'outro_bureau_quod':    'Quod',
            'outro_bureau_outro':   'Outro Bureau (não listado)',
        }
        bureaus_no_doc_texts = [fk for fk in BUREAU_LABELS if fk in doc_texts]
        bureau_primario_fk = None
        bureaus_secundarios_fks = []
        if 'bureau' in bureaus_no_doc_texts:
            bureau_primario_fk = 'bureau'
            bureaus_secundarios_fks = [fk for fk in bureaus_no_doc_texts if fk != 'bureau']
        elif bureaus_no_doc_texts:
            bureau_primario_fk = bureaus_no_doc_texts[0]
            bureaus_secundarios_fks = bureaus_no_doc_texts[1:]

        bureau_primario_label = BUREAU_LABELS.get(bureau_primario_fk, '—') if bureau_primario_fk else '—'
        bureaus_secundarios_labels = [BUREAU_LABELS[fk] for fk in bureaus_secundarios_fks]
        print(f"[ANALYSIS {analysis_id[:8]}] Bureau primário: {bureau_primario_label}. "
              f"Secundários: {', '.join(bureaus_secundarios_labels) if bureaus_secundarios_labels else '(nenhum)'}", flush=True)

        # Renomeia chaves do doc_texts para deixar a hierarquia explícita no prompt
        if bureau_primario_fk:
            texto_primario = doc_texts.pop(bureau_primario_fk)
            doc_texts[f'BUREAU_PRIMARIO_{bureau_primario_label.upper().replace(" ", "_")}'] = (
                f"[Este é o bureau PRIMÁRIO desta análise — fonte principal de dados de bureau]\n{texto_primario}"
            )
            for fk in bureaus_secundarios_fks:
                texto_sec = doc_texts.pop(fk)
                lbl_sec = BUREAU_LABELS[fk]
                doc_texts[f'BUREAU_SECUNDARIO_{lbl_sec.upper().replace(" ", "_")}'] = (
                    f"[Bureau SECUNDÁRIO — referência adicional para corroborar ou complementar o primário]\n{texto_sec}"
                )
        # ===== ETAPA 4 FIM =====

        docs_compiled = "\n\n".join(
            f"=== {key.upper()} ===\n{text}"
            for key, text in doc_texts.items()
        )

        diretrizes_bloco = ""
        if diretrizes and diretrizes.strip():
            diretrizes_bloco = f"""
DIRETRIZES DA EMPRESA ANALISADORA (seguir obrigatoriamente):
{diretrizes}

"""

        prompt = f"""Voce e um analista senior de credito com experiencia em industria, comercio, servicos e agronegocio.
{diretrizes_bloco}Analise os documentos abaixo e produza um relatorio completo de credito em JSON.

DOCUMENTOS DISPONIVEIS:
{docs_compiled}

Produza o relatorio em JSON valido com exatamente esta estrutura:

{{
  "empresa": {{
    "nome": "",
    "cnpj": "",
    "nome_fantasia": "",
    "fundacao": "",
    "regime_tributario": "",
    "capital_social": 0,
    "porte": "",
    "cnae": "",
    "situacao_cadastral": ""
  }},
  "scores": {{
    "bureau": 0,
    "vibra_composto": 0,
    "comportamental": 0,
    "financeiro": 0,
    "cadastral": 0,
    "tributario": 0,
    "garantias": 0,
    "cobertura_documental": 0
  }},
  "indicadores": {{
    "liquidez_corrente": null,
    "liquidez_seca": null,
    "liquidez_geral": null,
    "endividamento_pl": null,
    "margem_bruta": null,
    "margem_operacional": null,
    "margem_liquida": null,
    "pmr_dias": null,
    "pmp_dias": null,
    "ciclo_financeiro": null,
    "ncg": null,
    "endiv_fat_meses": null,
    "faturamento_medio_mensal": null,
    "scr_total": null,
    "scr_vencido": null
  }},
  "periodos_financeiros": [
    {{
      "periodo": "AAAA ou AAAA/MM-MM (ex: 2023 ou 2024/01-12)",
      "tipo_documento": "Balanço Patrimonial | DRE | ambos",
      "receita_bruta": null,
      "receita_liquida": null,
      "resultado_bruto": null,
      "resultado_operacional": null,
      "resultado_liquido": null,
      "ebitda": null,
      "ebitda_margem": null,
      "margem_bruta": null,
      "margem_operacional": null,
      "margem_liquida": null,
      "liquidez_corrente": null,
      "liquidez_seca": null,
      "liquidez_geral": null,
      "endividamento_pl": null,
      "pmr_dias": null,
      "pmp_dias": null,
      "ciclo_financeiro": null,
      "ncg": null,
      "ativo_total": null,
      "patrimonio_liquido": null,
      "divida_total": null
    }}
  ],
  "financeiro_calculado": {{
    "ebitda": null,
    "ebitda_margem": null,
    "margem_bruta": null,
    "margem_operacional": null,
    "margem_liquida": null,
    "receita_bruta": null,
    "receita_liquida": null,
    "resultado_bruto": null,
    "resultado_operacional": null,
    "resultado_liquido": null,
    "memoria_calculo": "",
    "interpretacao": ""
  }},
  "limite": {{
    "recomendado": 0,
    "base_calculo": "",
    "memoria_calculo": "",
    "requer_excecao": false,
    "teto_politica": 0
  }},
  "parecer": "",
  "pontos_fortes": [""],
  "pontos_atencao": [
    {{"nivel": "alto", "descricao": ""}},
    {{"nivel": "medio", "descricao": ""}}
  ],
  "condicionantes": [
    {{"numero": 1, "descricao": ""}}
  ],
  "tributario": {{
    "pefin_refin": "",
    "pefin_total": null,
    "pefin_qtd": null,
    "refin_total": null,
    "refin_qtd": null,
    "protestos_total": null,
    "protestos_qtd": null,
    "cheques_sem_fundo": "",
    "divida_ativa": null,
    "divida_ativa_desc": "",
    "parcelamentos_status": ""
  }},
  "endividamento_scr": [
    {{"modalidade": "", "valor": 0, "prazo": "CP"}}
  ],
  "faturamento_periodos": [
    {{
      "periodo": "AAAA ou semestre/ano (ex: 2023, 2024, 1S2024)",
      "total_anual": null,
      "media_mensal": null,
      "meses": [
        {{"competencia": "MM/AAAA", "valor": 0, "variacao_ah": null}}
      ]
    }}
  ],
  "faturamento_mensal": [
    {{"competencia": "MM/AAAA", "valor": 0, "variacao_ah": null}}
  ],
  "endividamento_privado": [
    {{"credor": "", "modalidade": "", "valor": 0}}
  ],
  "processos": {{
    "total": 0,
    "como_autora": 0,
    "como_reu": 0,
    "valor_total": 0,
    "analise_geral": "",
    "lista": [
      {{"numero": "", "tipo": "", "polo": "", "vara": "", "assunto": "", "valor": 0, "analise": ""}}
    ]
  }},
  "cadastral": {{
    "site": "",
    "funcionarios": "",
    "endereco": "",
    "representantes": [
      {{"nome": "", "cpf": "", "papel": ""}}
    ],
    "alteracoes": [
      {{"data": "", "descricao": ""}}
    ]
  }},
  "qsa": [
    {{
      "nome": "",
      "participacao_pct": 0,
      "cargo": "",
      "geracao": "",
      "analise_geracional": "",
      "patrimonio_declarado": null,
      "dividas_declaradas": null,
      "bens_arrestaveis": null,
      "irpf_disponivel": true,
      "score_gestor": null
    }}
  ],
  "grupo_economico": [
    {{"empresa": "", "cnpj": "", "situacao": "", "relacao": "", "restricoes": ""}}
  ],
  "historico_interno": {{
    "tempo_relacionamento": "",
    "volume_medio": "",
    "pontualidade": "",
    "inadimplencia": "",
    "obs": "",
    "analise_ia": ""
  }},
  "curva_abc": [
    {{
      "cliente": "",
      "cnpj_cpf": "",
      "participacao_pct": 0,
      "valor_anual": null,
      "segmento": ""
    }}
  ],
  "bureau_utilizado": {{
    "primario": "",
    "secundarios": []
  }}
}}

INSTRUCOES GERAIS:
- Responda APENAS o JSON, sem texto antes ou depois, sem markdown
- Use null para campos sem dados disponíveis

INSTRUCOES SOBRE SCORES (CRITICO):
- TODOS os scores em "scores" devem estar na escala 0 a 1000 (mesma escala usada por Serasa/Boa Vista).
- 0 = pior (alto risco / dados inexistentes), 1000 = melhor (perfeito).
- Avalie cada componente de forma independente:
  * bureau (0-1000): score do bureau de credito (Vibra Full ou outro). Se Vibra Full presente, use score do Vibra Full. Se ausente, use o bureau primario disponivel.
  * comportamental (0-1000): historico de pagamentos, restricoes (PEFIN/REFIN), protestos, cheques sem fundo. 1000 = sem restricoes.
  * financeiro (0-1000): liquidez, rentabilidade, endividamento, ciclo. 1000 = saude financeira excelente.
  * cadastral (0-1000): situacao na Receita, idade da empresa, regime tributario, QSA estavel. 1000 = totalmente regular.
  * tributario (0-1000): regularidade fiscal, certidoes negativas, parcelamentos. 1000 = sem pendencias.
  * garantias (0-1000): qualidade dos avalistas, bens arrestaveis, contratos. 1000 = garantias robustas.
  * cobertura_documental (0-1000): quantos documentos esperados foram fornecidos e validados. 1000 = todos presentes e validos.
- Deixe "vibra_composto" como 0 (sera calculado pelo motor com base nos pesos configurados).
- A classe (A a J) tambem sera atribuida pelo motor com base nos limites configurados.

INSTRUCOES SOBRE BUREAUS (CRITICO):
- Pode haver uma secao "BUREAU_PRIMARIO_..." e zero ou mais "BUREAU_SECUNDARIO_..." nos documentos.
- Use o BUREAU PRIMARIO como fonte principal para score de bureau, pendencias (PEFIN/REFIN), protestos, dividas, cheques, processos judiciais e quadro societario.
- Use os BUREAUS SECUNDARIOS APENAS para CORROBORAR ou COMPLEMENTAR informacoes do primario (ex: confirmar pendencias listadas, identificar protestos adicionais, validar processos juridicos).
- Em caso de divergencia entre primario e secundario, sempre PREFIRA o primario, mas mencione a divergencia nos pontos_atencao.
- No campo "bureau_utilizado.primario" coloque o nome do bureau primario (ex: "Vibra Full", "Serasa Experian", "Boa Vista SCPC", "SPC Brasil", "Quod").
- No campo "bureau_utilizado.secundarios" coloque uma lista com os nomes dos bureaus secundarios usados como referencia (ex: ["Serasa Experian", "Boa Vista SCPC"]).
- Se nenhum bureau foi enviado, deixe primario como "" e secundarios como [].

INSTRUCOES CRITICAS — ANALISE MULTI-PERIODO:
- IDENTIFICACAO DE PERIODOS: Antes de qualquer coisa, identifique TODOS os períodos distintos presentes nos documentos enviados (ex: DRE 2022, DRE 2023, Balanco 2022, Balanco 2023, declaracao faturamento jan-jun/2024, etc.)
- PERIODOS_FINANCEIROS: Para cada periodo identificado em Balanco Patrimonial e/ou DRE, crie um objeto separado no array "periodos_financeiros". Calcule todos os indicadores para CADA periodo individualmente. Nao consolide — mantenha separado.
- FATURAMENTO_PERIODOS: Para cada arquivo de faturamento (Arquivo 1, Arquivo 2, etc.), identifique o periodo correspondente e crie um objeto separado no array "faturamento_periodos". Dentro de cada objeto, liste os meses de "meses" extraídos daquele arquivo. Calcule total_anual e media_mensal para cada periodo.
- FATURAMENTO_MENSAL: Mantenha tambem o array "faturamento_mensal" com TODOS os meses de TODOS os periodos concatenados em ordem cronologica (para compatibilidade).
- FINANCEIRO_CALCULADO: Use os dados do periodo mais recente disponivel. Inclua memoria_calculo e interpretacao comparando a evolucao entre os periodos identificados.
- INDICADORES: Use os valores do periodo mais recente.
- Para tributario: extraia PEFIN, REFIN, Protestos, Cheques sem fundo, Dívida Ativa e Parcelamentos do bureau. Em "pefin_refin" coloque um resumo textual (ex: "PEFIN: R$ 6,0MM (5 ocorr.) | REFIN: R$ 7,5MM (3 ocorr.) | Protestos: R$ 52,5MM (41 ocorr.)"). Se não houver, escreva "Nada consta". Preencha os valores numéricos nos campos individuais.
- Para processos: leia TODOS os processos da secao "Processos" do bureau. Selecione ate 6 processos criticos para incluir na lista, priorizando nesta ordem:
  1. Polo: empresa como REU tem prioridade sobre AUTOR
  2. Status: ATIVO e SUSPENSO com movimentacao recente primeiro; ARQUIVADO e BAIXADO por ultimo
  3. Palavras-chave criticas: qualquer processo contendo CRIMINAL, RECUPERACAO JUDICIAL, LIMINAR ou BANCARIO sobe na selecao independente de valor
  4. Valor financeiro: maior valor expresso tem prioridade (independente de ser credor publico ou privado)
  5. Data: movimentacao mais recente tem prioridade
  6. Tipo: Cumprimento de Sentenca e Execucao Fiscal entram antes de processos em fase inicial (Distribuido, Peticao)
  Para cada processo selecionado, preencha numero, tipo, vara_tribunal, assunto, polo (REU/AUTOR/OUTRO), status, valor e analise_critica (maximo 1 frase curta e objetiva explicando o risco)
- Para grupo_economico: extraia empresas relacionadas ativas do bureau
- Para cadastral: extraia site, funcionarios, representantes e alteracoes do bureau ou contrato social
- Para historico_interno: se houver dados preenchidos pelo analista, gere analise_ia avaliando o comportamento historico
- Para qsa: inclua dados de PEP, sancoes e perfil do bureau se disponivel
- Para curva_abc: se houver documento de Curva ABC, extraia cada cliente com participacao percentual
- Para endividamento_privado: - Para endividamento_privado: extraia EXCLUSIVAMENTE de documentos anexados separadamente que contenham dados de endividamento privado (ex: planilhas de dividas, extratos bancarios, relatorios de endividamento, documentos SCR/BACEN separados do bureau, declaracoes de dividas). NAO extraia do documento de bureau (Vibra Full ou qualquer outro bureau de credito) — os dados de PEFIN/REFIN/Protestos do bureau ja estao capturados no campo "tributario". Se nenhum documento separado de endividamento foi anexado, retorne array vazio []. Para cada credor identificado nos documentos separados, preencha: "credor" (nome da instituicao ou pessoa), "modalidade" (tipo de divida, ex: "CCB - Banco X", "Contrato de Mutuo", "Debito Bancario", "CRI", "CRA", "Debenture"), "valor" (valor em reais)."""

        client = _get_client()
        raw_parts = []

        # ===== ETAPA 3 INICIO — Streaming resiliente: retry/backoff + max_tokens 48000 =====
        import time
        import random
        MAX_TENTATIVAS = 3
        ERROS_TRANSITORIOS = (
            "RemoteProtocolError",
            "incomplete chunked read",
            "Data-loss while decompressing",
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
                print(f"[ANALYSIS {analysis_id[:8]}] Chamando Claude (tentativa {tentativa}/{MAX_TENTATIVAS}) "
                      f"max_tokens=48000, prompt={len(prompt)} chars...", flush=True)
                t_inicio = time.time()
                raw_parts = []
                with client.messages.stream(
                    model="claude-sonnet-4-5",
                    max_tokens=48000,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    for text in stream.text_stream:
                        raw_parts.append(text)
                t_decorrido = time.time() - t_inicio
                print(f"[ANALYSIS {analysis_id[:8]}] ✓ Resposta recebida em {t_decorrido:.1f}s, "
                      f"{sum(len(p) for p in raw_parts)} chars.", flush=True)
                break
            except Exception as ex_stream:
                err_name = type(ex_stream).__name__
                err_msg = str(ex_stream)[:200]
                print(f"[ANALYSIS {analysis_id[:8]}] ✗ Erro tentativa {tentativa}: {err_name}: {err_msg}", flush=True)
                ultimo_erro = ex_stream
                transitorio = any(t in err_name or t in err_msg for t in ERROS_TRANSITORIOS)
                if tentativa < MAX_TENTATIVAS and transitorio:
                    backoff = 5 * tentativa + random.uniform(0, 2)
                    print(f"[ANALYSIS {analysis_id[:8]}] Erro transitório, aguardando {backoff:.1f}s antes do retry...", flush=True)
                    time.sleep(backoff)
                else:
                    if not transitorio:
                        print(f"[ANALYSIS {analysis_id[:8]}] Erro NÃO transitório — sem retry.", flush=True)
                    raise ultimo_erro
        # ===== ETAPA 3 FIM =====

        raw = "".join(raw_parts).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        try:
            result = json.loads(raw)
        except Exception:
            for suffix in ['', '}', '"}}', '"]}', '"]}}}', '"]}}}}}', '"]}}}}}}']:
                try:
                    result = json.loads(raw + suffix)
                    break
                except Exception:
                    continue
            else:
                raise ValueError("JSON invalido mesmo apos tentativas de recuperacao")

        from models.database import Report as R
        report = db.query(R).filter(R.analysis_id == analysis_id).first()
        if not report:
            report = R(analysis_id=analysis_id)
            db.add(report)

        scores = result.get("scores", {})
        ind    = result.get("indicadores", {})
        emp    = result.get("empresa", {})
        lim    = result.get("limite", {})

        # ===== ETAPA 5 INICIO — Recalcular vibra_composto com pesos do banco =====
        # IA agora dá scores em escala 0-1000. Aplicamos pesos do ScoringConfig
        # para obter vibra_composto determinístico e auditável.
        try:
            from models.database import ScoringConfig
            cfg = db.query(ScoringConfig).filter(ScoringConfig.id == 1).first()
            if not cfg:
                # Não existe ainda — usa defaults
                pesos = {
                    "bureau": 25.0, "financeiro": 25.0, "comportamental": 15.0,
                    "cadastral": 10.0, "tributario": 10.0, "garantias": 10.0,
                    "cobertura": 5.0,
                }
                limites = {"a":900,"b":800,"c":700,"d":600,"e":500,
                           "f":400,"g":300,"h":200,"i":100}
            else:
                pesos = {
                    "bureau": cfg.peso_bureau, "financeiro": cfg.peso_financeiro,
                    "comportamental": cfg.peso_comportamental, "cadastral": cfg.peso_cadastral,
                    "tributario": cfg.peso_tributario, "garantias": cfg.peso_garantias,
                    "cobertura": cfg.peso_cobertura,
                }
                limites = {"a":cfg.limite_a,"b":cfg.limite_b,"c":cfg.limite_c,
                           "d":cfg.limite_d,"e":cfg.limite_e,"f":cfg.limite_f,
                           "g":cfg.limite_g,"h":cfg.limite_h,"i":cfg.limite_i}
            # Sub-scores em 0-1000 (a IA agora deve obedecer essa escala — ver prompt)
            sub = {
                "bureau":         float(scores.get("bureau") or 0),
                "financeiro":     float(scores.get("financeiro") or 0),
                "comportamental": float(scores.get("comportamental") or 0),
                "cadastral":      float(scores.get("cadastral") or 0),
                "tributario":     float(scores.get("tributario") or 0),
                "garantias":      float(scores.get("garantias") or 0),
                "cobertura":      float(scores.get("cobertura_documental") or 0),
            }
            composto = sum(sub[k] * (pesos[k] / 100.0) for k in sub)
            composto = max(0.0, min(1000.0, composto))
            # Classifica
            if   composto >= limites["a"]: classe = "A"
            elif composto >= limites["b"]: classe = "B"
            elif composto >= limites["c"]: classe = "C"
            elif composto >= limites["d"]: classe = "D"
            elif composto >= limites["e"]: classe = "E"
            elif composto >= limites["f"]: classe = "F"
            elif composto >= limites["g"]: classe = "G"
            elif composto >= limites["h"]: classe = "H"
            elif composto >= limites["i"]: classe = "I"
            else:                           classe = "J"
            # Sobrescreve no result (frontend lê de raw_json) e variáveis
            scores["vibra_composto"] = round(composto, 1)
            scores["_memoria_calculo"] = (
                f"Vibra Composto = "
                + " + ".join([f"{k}({sub[k]:.0f})×{pesos[k]:.1f}%" for k in sub])
                + f" = {composto:.1f} → Classe {classe}"
            )
            scores["_classe"] = classe
            scores["_pesos_aplicados"] = pesos
            result["scores"] = scores
            print(f"[ANALYSIS {analysis_id[:8]}] Score recalculado: "
                  f"composto={composto:.1f}, classe={classe}", flush=True)
        except Exception as ex_score:
            print(f"[ANALYSIS {analysis_id[:8]}] Aviso ao recalcular score: {ex_score}", flush=True)
            classe = None
        # ===== ETAPA 5 FIM =====

        report.score_bureau          = scores.get("bureau")
        report.score_vibra           = scores.get("vibra_composto")
        report.score_comportamental  = scores.get("comportamental")
        report.score_financeiro      = scores.get("financeiro")
        report.score_cadastral       = scores.get("cadastral")
        report.score_tributario      = scores.get("tributario")
        report.score_garantias       = scores.get("garantias")
        report.score_cobertura       = scores.get("cobertura_documental")
        # ETAPA 5 — persiste a classe calculada também
        try:
            if classe:
                report.score_classe = classe
        except Exception:
            pass
        report.liquidez_corrente     = ind.get("liquidez_corrente")
        report.liquidez_seca         = ind.get("liquidez_seca")
        report.endiv_pl              = ind.get("endividamento_pl")
        report.margem_liquida        = ind.get("margem_liquida")
        report.pmr_dias              = ind.get("pmr_dias")
        report.pmp_dias              = ind.get("pmp_dias")
        report.ciclo_financeiro      = ind.get("ciclo_financeiro")
        report.ncg                   = ind.get("ncg")
        report.endiv_fat             = ind.get("endiv_fat_meses")
        report.limite_recomendado    = lim.get("recomendado")
        report.limite_calc_memo      = lim.get("memoria_calculo")
        report.parecer               = result.get("parecer")
        report.pontos_fortes         = json.dumps(result.get("pontos_fortes", []))
        report.pontos_atencao        = json.dumps(result.get("pontos_atencao", []))
        report.condicionantes        = json.dumps(result.get("condicionantes", []))
        report.qsa_analise           = json.dumps(result.get("qsa", []))
        report.grupo_economico       = json.dumps(result.get("grupo_economico", []))
        report.empresa_nome          = emp.get("nome")
        report.empresa_cnpj          = emp.get("cnpj")
        report.empresa_fantasia      = emp.get("nome_fantasia")
        report.empresa_fundacao      = emp.get("fundacao")
        report.empresa_regime        = emp.get("regime_tributario")
        report.empresa_capital       = emp.get("capital_social")
        report.raw_json              = json.dumps(result, ensure_ascii=False)

        db_analysis.status       = "done"
        db_analysis.company_name = emp.get("nome")
        db_analysis.cnpj         = emp.get("cnpj")
        db.commit()

        return result

    except Exception as e:
        import traceback
        print("=" * 60)
        print(f"[ANALYSIS {analysis_id[:8]}] ERRO NA ANALISE:", str(e))
        traceback.print_exc()
        print("=" * 60)
        db_analysis.status = "error"
        # ===== ETAPA 3 INICIO — salva mensagem de erro para o frontend exibir =====
        try:
            from models.database import Report as R_err
            report_err = db.query(R_err).filter(R_err.analysis_id == analysis_id).first()
            if not report_err:
                report_err = R_err(analysis_id=analysis_id)
                db.add(report_err)
            # Reaproveita campo parecer para mostrar a mensagem (Parecer IA é bloqueado em edição, então fica claro)
            msg_curta = f"{type(e).__name__}: {str(e)[:400]}"
            report_err.parecer = (
                f"Análise não pôde ser concluída.\n\n"
                f"Motivo técnico: {msg_curta}\n\n"
                f"Tente novamente. Se o erro persistir, reduza a quantidade de documentos enviados "
                f"(limite: 20 documentos por análise)."
            )
        except Exception:
            pass
        # ===== ETAPA 3 FIM =====
        db.commit()
        raise e

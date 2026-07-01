"""
Vibra Score — Servico de validacao de documentos via Claude API
Rodada 1: separacao leitura x compatibilidade + deteccao de defasagem

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
"""

import anthropic
import pdfplumber
import io
import json
import os
from datetime import datetime, date
from dotenv import load_dotenv

# Carrega .env do diretório do backend antes de qualquer leitura de variável

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

def _get_client() -> anthropic.Anthropic:
    """Instancia o client Anthropic apenas quando necessário, após o .env ser carregado."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY não encontrada. "
            "Crie o arquivo backend/.env com ANTHROPIC_API_KEY=sua-chave e reinicie o servidor."
        )
    return anthropic.Anthropic(api_key=api_key)

# Defasagem maxima aceitavel por tipo de documento (em dias)
MAX_DEFASAGEM = {
    "bureau":          30,   # critico — 30 dias
    "scr":             60,   # critico — 60 dias
    "faturamento":     90,   # 3 meses
    "balanco":        365,   # 1 exercicio
    "dre":            365,
    "irpf":           730,   # 2 anos
    "contrato":      1825,   # 5 anos
    "endividamento":   60,
    "certidoes":       90,
    "laudo":          730,
    "curva":          180,
    "historico":      180,
}

FIELD_DEFINITIONS = {
    "balanco": {
        "label": "Balanco Patrimonial ou Balancete",
        "description": "Demonstrativo contabil com Ativo, Passivo e Patrimonio Liquido.",
        "keywords": ["ativo","passivo","patrimonio","balanco","balancete","circulante"],
    },
    "dre": {
        "label": "DRE — Demonstracao do Resultado",
        "description": "Demonstrativo com receita, custos, despesas e resultado.",
        "keywords": ["receita","resultado","lucro","prejuizo","DRE","demonstracao"],
    },
    "scr": {
        "label": "SCR / BACEN",
        "description": "Relatorio do Sistema de Informacoes de Credito do Banco Central.",
        "keywords": ["SCR","BACEN","banco central","sistema de credito","modalidade","vencido","a vencer"],
    },
    "bureau": {
        "label": "Bureau de Credito (Vibra Full ou equivalente)",
        "description": "Relatorio de bureau com score, restricoes, processos e dados comportamentais.",
        "keywords": ["score","restricoes","protestos","PEFIN","REFIN","processos","bureau","serasa","boa vista"],
    },
    "faturamento": {
        "label": "Declaracao de Faturamento",
        "description": "Documento com historico de faturamento mensal.",
        "keywords": ["faturamento","receita bruta","mensal","notas fiscais","NF","declaracao"],
    },
    "endividamento": {
        "label": "Quadro de Endividamento",
        "description": "Listagem de dividas com bancos, FIDCs, securitizadoras, factorings, PRONAMPE, FINIMP, CCB, NCE, ACC, contratos de credito.",
        "keywords": ["divida","credito","FIDC","factoring","saldo","contrato","banco","PRONAMPE","FINIMP","CCB","NCE","ACC","endividamento","financiamento"],
    },
    "irpf": {
        "label": "IRPF — Imposto de Renda Pessoa Fisica",
        "description": "Declaracao de IR PF com bens, rendimentos e dividas.",
        "keywords": ["IRPF","imposto de renda","bens e direitos","rendimentos","CPF","declaracao","DIRPF"],
    },
    "contrato": {
        "label": "Contrato Social / Alteracao Contratual",
        "description": "Documento constitutivo da empresa com quadro societario.",
        "keywords": ["contrato social","alteracao","socios","objeto social","CNPJ","junta comercial"],
    },
    "certidoes": {
        "label": "Certidoes Negativas",
        "description": "Certidoes negativas federal, estadual, municipal, FGTS ou trabalhista.",
        "keywords": ["certidao","negativa","debitos","tributos","FGTS","trabalhista"],
    },
    "laudo": {
        "label": "Laudo de Avaliacao de Imovel",
        "description": "Laudo tecnico de avaliacao de imovel com valor de mercado.",
        "keywords": ["laudo","avaliacao","imovel","CREA","CRECI","valor de mercado"],
    },
    "curva": {
        "label": "Curva ABC de Clientes",
        "description": "Listagem de clientes com participacao no faturamento.",
        "keywords": ["clientes","ABC","faturamento","participacao","concentracao"],
    },
    "historico": {
        "label": "Historico de Relacionamento Interno",
        "description": "Dados de comportamento do cliente na carteira.",
        "keywords": ["historico","relacionamento","carteira","cliente","operacoes"],
    },
}


def extract_text_from_pdf(file_bytes: bytes) -> tuple[str, float]:
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = pdf.pages
            total = len(pages)
            if total == 0:
                return "", 0.0
            texts = []
            pages_ok = 0
            for page in pages:
                text = page.extract_text()
                if text and len(text.strip()) > 20:
                    texts.append(text)
                    pages_ok += 1
            full = "\n\n".join(texts)
            pct = round((pages_ok / total) * 100, 1)
            return full, pct
    except Exception:
        return "", 0.0


def calcular_defasagem(data_referencia_str: str, field_key: str) -> dict:
    """
    Calcula a defasagem entre a data de referencia do documento e hoje.
    Retorna dict com: dias, nivel (ok/warn/critico), mensagem
    """
    if not data_referencia_str or data_referencia_str == "nao_identificada":
        return {
            "dias": None,
            "nivel": "warn",
            "mensagem": "Data de referencia nao identificada — confirme o periodo do documento"
        }

    try:
        # Tentar varios formatos de data
        formatos = ["%Y-%m-%d", "%d/%m/%Y", "%m/%Y", "%Y"]
        data_ref = None
        for fmt in formatos:
            try:
                data_ref = datetime.strptime(data_referencia_str.strip(), fmt).date()
                # Se so tem ano/mes, usar ultimo dia do mes
                break
            except ValueError:
                continue

        if not data_ref:
            return {"dias": None, "nivel": "warn", "mensagem": "Formato de data nao reconhecido"}

        hoje = date.today()
        dias = (hoje - data_ref).days
        limite = MAX_DEFASAGEM.get(field_key, 180)

        if dias <= limite:
            return {
                "dias": dias,
                "nivel": "ok",
                "mensagem": f"Documento dentro do prazo — {dias} dias de defasagem"
            }
        elif dias <= limite * 2:
            return {
                "dias": dias,
                "nivel": "warn",
                "mensagem": f"Defasagem elevada — {dias} dias (limite recomendado: {limite} dias). Verifique se e necessario documento mais recente."
            }
        else:
            return {
                "dias": dias,
                "nivel": "critico",
                "mensagem": f"Defasagem critica — {dias} dias (limite: {limite} dias). Fortemente recomendado substituir por versao mais recente antes de prosseguir."
            }
    except Exception:
        return {"dias": None, "nivel": "warn", "mensagem": "Nao foi possivel calcular a defasagem"}


def validate_document_with_ai(
    field_key: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
) -> dict:
    """
    Valida o documento via Claude.
    Retorna:
      - read_pct: % de leitura tecnica do PDF
      - compatibility_pct: % de compatibilidade com o campo
      - compatibility_level: ok / warn / error
      - compatibility_msg: mensagem de compatibilidade
      - defasagem: dict com nivel e mensagem
      - is_valid: bool
      - message: mensagem resumida para o frontend
    """
    field_def = FIELD_DEFINITIONS.get(field_key, {})
    field_label = field_def.get("label", field_key)
    field_description = field_def.get("description", "")

    # Arquivos de midia
    if field_key in ("visita_audio", "visita_video", "visita_fotos"):
        return {
            "is_valid": True,
            "read_pct": 100.0,
            "compatibility_pct": 100,
            "compatibility_level": "ok",
            "compatibility_msg": "Arquivo de midia recebido",
            "defasagem": {"dias": None, "nivel": "ok", "mensagem": ""},
            "message": "Arquivo de midia recebido — sera processado durante a analise.",
        }

    # Imagem em campo de PDF
    is_image = mime_type.startswith("image/") or filename.lower().endswith(
        (".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp")
    )
    if is_image:
        return {
            "is_valid": False,
            "read_pct": 0.0,
            "compatibility_pct": 0,
            "compatibility_level": "error",
            "compatibility_msg": "Imagem enviada — este campo requer PDF",
            "defasagem": {"dias": None, "nivel": "warn", "mensagem": ""},
            "message": "Imagem enviada. Este campo requer PDF. Por favor substitua o arquivo.",
        }

    # Extrair texto
    extracted_text, read_pct = extract_text_from_pdf(file_bytes)

    if not extracted_text or read_pct < 5:
        return {
            "is_valid": None,
            "read_pct": read_pct,
            "compatibility_pct": None,
            "compatibility_level": "warn",
            "compatibility_msg": "PDF sem texto extraivel — possivel escaneado sem OCR",
            "defasagem": {"dias": None, "nivel": "warn", "mensagem": "Data nao identificavel em PDF escaneado"},
            "message": f"PDF sem texto extraivel (leitura: {read_pct}%). Para melhor resultado, envie a versao digital.",
        }

    text_sample = extracted_text[:6000]

    prompt = f"""Voce e um validador de documentos financeiros. Analise o texto extraido abaixo e responda em JSON:

Campo esperado: {field_label}
Descricao: {field_description}

Texto extraido:
---
{text_sample}
---

Responda APENAS em JSON valido:
{{
  "doc_type_found": "tipo de documento identificado",
  "matches_field": true ou false,
  "compatibility_pct": 0 a 100,
  "read_quality_pct": 0 a 100,
  "data_referencia": "data de referencia principal do documento no formato YYYY-MM-DD ou MM/YYYY ou YYYY, ou nao_identificada",
  "message_pt": "mensagem curta em portugues (max 120 chars)",
  "is_balancete": true ou false,
  "has_dre_together": true ou false,
  "observacoes": "observacoes tecnicas relevantes sobre o documento (max 150 chars)"
}}"""

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())

        is_valid = result.get("matches_field", False)
        doc_type = result.get("doc_type_found", "Desconhecido")
        compat_pct = result.get("compatibility_pct", 0)
        quality = result.get("read_quality_pct", read_pct)
        msg = result.get("message_pt", "")
        is_balancete = result.get("is_balancete", False)
        has_dre = result.get("has_dre_together", False)
        data_ref = result.get("data_referencia", "nao_identificada")
        observacoes = result.get("observacoes", "")

        # is_valid agora exige compatibilidade mínima de 50%
        is_valid = matches_field and compat_pct >= 50
        # Calcular defasagem
        defasagem = calcular_defasagem(data_ref, field_key)

        # Nivel de compatibilidade
        if is_valid and compat_pct >= 70:
            compat_level = "ok"
            extras = []
            if is_balancete:
                extras.append("balancete identificado")
            if has_dre:
                extras.append("DRE incluido no mesmo arquivo")
            extra_str = " · " + " · ".join(extras) if extras else ""
            compat_msg = f"Compativel com o campo ({compat_pct}%){extra_str}"
        elif is_valid and compat_pct >= 50:
            compat_level = "warn"
            compat_msg = f"Compatibilidade parcial ({compat_pct}%) — {msg}"
        else:
            compat_level = "error"
            compat_msg = f"Documento identificado como: {doc_type}. Esperado: {field_label}. {msg}"

        return {
            "is_valid": is_valid,
            "doc_type_found": doc_type,
            "read_pct": quality,
            "compatibility_pct": compat_pct,
            "compatibility_level": compat_level,
            "compatibility_msg": compat_msg,
            "defasagem": defasagem,
            "data_referencia": data_ref,
            "is_balancete": is_balancete,
            "has_dre_together": has_dre,
            "observacoes": observacoes,
            "message": compat_msg,
        }

    except Exception as e:
        print(f"[VALIDATOR ERROR] field={field_key} erro={type(e).__name__}: {str(e)}", flush=True)
        # Fallback heuristico
        keywords = field_def.get("keywords", [])
        text_lower = extracted_text.lower()
        hits = sum(1 for kw in keywords if kw.lower() in text_lower)
        passes = hits >= 2 or len(keywords) == 0
        compat_pct = min(100, hits * 20) if keywords else 50

        return {
            "is_valid": passes,
            "doc_type_found": "Verificacao parcial (IA indisponivel)",
            "read_pct": read_pct,
            "compatibility_pct": compat_pct,
            "compatibility_level": "ok" if passes else "warn",
            "compatibility_msg": f"{'Compativel' if passes else 'Verificar'} ({compat_pct}%) — validacao basica",
            "defasagem": {"dias": None, "nivel": "warn", "mensagem": "Defasagem nao calculada (IA indisponivel)"},
            "message": f"{'Compativel' if passes else 'Verifique o documento'} — leitura {read_pct}%",
        }

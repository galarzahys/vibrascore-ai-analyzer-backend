"""
Models — SQLite via SQLAlchemy
Rodada P: adicionado suporte a Grupo Econômico (tabelas Grupo + GrupoDocument
e colunas grupo_id/ordem_no_grupo em Analysis — todas nullable, retrocompatíveis).
"""

from sqlalchemy import create_engine, Column, String, Integer, Float, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import uuid
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vibrascore.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)


# ── MODELS ────────────────────────────────────────────────────

class Analysis(Base):
    __tablename__ = "analyses"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status      = Column(String, default="pending")   # pending | processing | done | error
    company_name = Column(String, nullable=True)
    cnpj        = Column(String, nullable=True)
    analyst_name = Column(String, nullable=True)
    package_level = Column(String, default="full")    # basic | pro | full
    historico_interno = Column(Text, nullable=True)   # JSON com dados do histórico preenchido pelo analista
    vibra_id  = Column(String, nullable=True)   # ex: 2606-01 (AAMM-seq)
    vibra_ver = Column(Integer, default=1)       # versão da análise (incrementa ao reabrir)
    client_id = Column(String, nullable=True)    # tenant (cliente Vibra)

    # Rodada P — vínculo opcional com Grupo Econômico
    # NULL = análise singular (comportamento 100% retrocompatível)
    # preenchido = análise filha de um grupo econômico
    grupo_id        = Column(String, nullable=True)
    ordem_no_grupo  = Column(Integer, nullable=True)  # 1, 2, 3, 4 — ordem visual no grupo

    documents   = relationship("Document", back_populates="analysis", cascade="all, delete-orphan")
    report      = relationship("Report", back_populates="analysis", uselist=False, cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id     = Column(String, ForeignKey("analyses.id"), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Tipo esperado pelo campo
    field_key       = Column(String, nullable=False)   # "balanco", "scr", "bureau", etc.
    field_label     = Column(String, nullable=True)

    # Arquivo
    original_name   = Column(String, nullable=False)
    s3_key          = Column(String, nullable=True)    # caminho no S3
    file_size       = Column(Integer, nullable=True)
    mime_type       = Column(String, nullable=True)

    # Validação
    is_valid        = Column(Boolean, nullable=True)   # None=não validado, True/False
    validation_msg  = Column(Text, nullable=True)
    read_pct        = Column(Float, nullable=True)     # % de leitura estimada pela IA
    doc_type_found  = Column(String, nullable=True)    # tipo identificado pela IA
    is_required     = Column(Boolean, default=True)

    analysis        = relationship("Analysis", back_populates="documents")


class Report(Base):
    __tablename__ = "reports"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id     = Column(String, ForeignKey("analyses.id"), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Scores
    score_bureau    = Column(Float, nullable=True)
    score_vibra     = Column(Float, nullable=True)
    score_comportamental = Column(Float, nullable=True)
    score_financeiro     = Column(Float, nullable=True)
    score_cadastral      = Column(Float, nullable=True)
    score_tributario     = Column(Float, nullable=True)
    score_garantias      = Column(Float, nullable=True)
    score_cobertura      = Column(Float, nullable=True)

    # Índices financeiros
    liquidez_corrente   = Column(Float, nullable=True)
    liquidez_seca       = Column(Float, nullable=True)
    endiv_pl            = Column(Float, nullable=True)
    margem_liquida      = Column(Float, nullable=True)
    pmr_dias            = Column(Float, nullable=True)
    pmp_dias            = Column(Float, nullable=True)
    ciclo_financeiro    = Column(Float, nullable=True)
    ncg                 = Column(Float, nullable=True)
    endiv_fat           = Column(Float, nullable=True)

    # Limite
    limite_recomendado  = Column(Float, nullable=True)
    limite_calc_memo    = Column(Text, nullable=True)

    # Textos gerados
    parecer             = Column(Text, nullable=True)
    pontos_fortes       = Column(Text, nullable=True)   # JSON
    pontos_atencao      = Column(Text, nullable=True)   # JSON
    condicionantes      = Column(Text, nullable=True)   # JSON
    qsa_analise         = Column(Text, nullable=True)   # JSON
    grupo_economico     = Column(Text, nullable=True)   # JSON

    # Dados cadastrais extraídos
    empresa_nome        = Column(String, nullable=True)
    empresa_cnpj        = Column(String, nullable=True)
    empresa_fantasia     = Column(String, nullable=True)
    empresa_fundacao     = Column(String, nullable=True)
    empresa_regime      = Column(String, nullable=True)
    empresa_capital     = Column(Float, nullable=True)

    # Raw JSON completo para referência
    raw_json            = Column(Text, nullable=True)

    # ETAPA 2 — overrides do analista (JSON) — {"<path>": {"valor":..., "por":..., "em":"ISO"}}
    overrides_json      = Column(Text, nullable=True)

    analysis            = relationship("Analysis", back_populates="report")


# ── RODADA P — GRUPO ECONÔMICO ────────────────────────────────

class Grupo(Base):
    """
    Grupo Econômico — agrega N análises (empresas filhas) e produz
    um relatório consolidado independente. Análises filhas continuam
    sendo Analysis normais (com grupo_id preenchido); o consolidado
    é gerado por um motor separado (Rodada R), sem alterar o motor
    de análise singular existente.
    """
    __tablename__ = "grupos"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Identificação
    nome        = Column(String, nullable=False)
    client_id   = Column(String, nullable=True)   # tenant (multi-tenant, mesmo padrão de Analysis)
    analista    = Column(String, nullable=True)
    diretrizes  = Column(Text, nullable=True)

    # ID Vibra do grupo (formato G + AAMM-NN, sequencial independente do singular)
    vibra_id    = Column(String, nullable=True)
    vibra_ver   = Column(Integer, default=1)

    # Estado da consolidação (preenchido pela Rodada R)
    consolidado_status   = Column(String, default="pending")   # pending | processing | done | error
    consolidado_raw_json = Column(Text, nullable=True)         # JSON completo da consolidação IA

    # Resultado da consolidação (campos diretos para acesso rápido)
    parecer_consolidado     = Column(Text, nullable=True)
    score_grupo             = Column(Float, nullable=True)
    limite_consolidado      = Column(Float, nullable=True)
    limite_soma_individual  = Column(Float, nullable=True)   # soma dos limites das filhas, para comparativo

    # Intercompany (decisão 1) — analista pode declarar valor/observações
    intercompany_obs        = Column(Text, nullable=True)

    documents_consolidados = relationship(
        "GrupoDocument", back_populates="grupo", cascade="all, delete-orphan"
    )


class GrupoDocument(Base):
    """
    Documento consolidado do grupo (ex: IRPF de sócio comum às 4 empresas).
    Vinculado ao Grupo, não a uma Analysis específica.
    Quando uma análise filha rodar, o motor recebe: docs da filha + docs consolidados.
    """
    __tablename__ = "grupo_documents"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    grupo_id        = Column(String, ForeignKey("grupos.id"), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Tipo esperado pelo campo (mesmo padrão de Document)
    field_key       = Column(String, nullable=False)
    field_label     = Column(String, nullable=True)

    # Arquivo
    original_name   = Column(String, nullable=False)
    s3_key          = Column(String, nullable=True)
    file_size       = Column(Integer, nullable=True)
    mime_type       = Column(String, nullable=True)

    # Validação (reusa services.document_validator.validate_document_with_ai)
    is_valid        = Column(Boolean, nullable=True)
    validation_msg  = Column(Text, nullable=True)
    read_pct        = Column(Float, nullable=True)
    doc_type_found  = Column(String, nullable=True)
    is_required     = Column(Boolean, default=False)   # docs consolidados são opcionais por design

    grupo           = relationship("Grupo", back_populates="documents_consolidados")


# ===== ETAPA 5 INICIO — ScoringConfig (singleton de calibração) =====
class ScoringConfig(Base):
    """
    Configuração global de calibração do Score Vibra.
    Tabela singleton: sempre id=1.
    Define os pesos de cada componente no cálculo do vibra_composto,
    e os limites de cada classe (A=melhor, J=pior) na escala 0-1000.
    """
    __tablename__ = "scoring_config"

    id = Column(Integer, primary_key=True, default=1)

    # Pesos dos 7 componentes (em %, soma deve dar 100)
    peso_bureau          = Column(Float, default=25.0)
    peso_financeiro      = Column(Float, default=25.0)
    peso_comportamental  = Column(Float, default=15.0)
    peso_cadastral       = Column(Float, default=10.0)
    peso_tributario      = Column(Float, default=10.0)
    peso_garantias       = Column(Float, default=10.0)
    peso_cobertura       = Column(Float, default=5.0)

    # Limites inferiores de cada classe na escala 0-1000.
    # Padrão: classes regulares de 100 pontos.
    # Classe J = qualquer valor abaixo de limite_i.
    limite_a = Column(Integer, default=900)
    limite_b = Column(Integer, default=800)
    limite_c = Column(Integer, default=700)
    limite_d = Column(Integer, default=600)
    limite_e = Column(Integer, default=500)
    limite_f = Column(Integer, default=400)
    limite_g = Column(Integer, default=300)
    limite_h = Column(Integer, default=200)
    limite_i = Column(Integer, default=100)

    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by  = Column(String, nullable=True)
# ===== ETAPA 5 FIM =====

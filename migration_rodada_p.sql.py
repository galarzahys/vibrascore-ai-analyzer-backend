-- =============================================================
-- Vibra Score — Rodada P: Migration de Grupo Econômico
-- =============================================================
-- Execute uma vez no SQLite local antes de subir o backend novo:
--
--   cd C:\Users\Casemiro\Downloads\vibrascore_mvp\vibrascore\backend
--   sqlite3 vibrascore.db < migration_rodada_p.sql
--
-- Idempotência: as duas linhas de ALTER TABLE podem falhar com
-- "duplicate column name" se já tiverem sido aplicadas. Isso é
-- esperado — ignore esses erros específicos. As demais
-- instruções usam IF NOT EXISTS e são 100% seguras para rerun.
-- =============================================================

-- ── ANALYSES: adicionar vínculo opcional com Grupo ─────────────
-- (NULL = análise singular, comportamento idêntico ao atual)

ALTER TABLE analyses ADD COLUMN grupo_id VARCHAR;
ALTER TABLE analyses ADD COLUMN ordem_no_grupo INTEGER;


-- ── NOVA TABELA: grupos ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS grupos (
    id                      VARCHAR PRIMARY KEY,
    created_at              DATETIME,
    updated_at              DATETIME,
    nome                    VARCHAR NOT NULL,
    client_id               VARCHAR,
    analista                VARCHAR,
    diretrizes              TEXT,
    vibra_id                VARCHAR,
    vibra_ver               INTEGER DEFAULT 1,
    consolidado_status      VARCHAR DEFAULT 'pending',
    consolidado_raw_json    TEXT,
    parecer_consolidado     TEXT,
    score_grupo             FLOAT,
    limite_consolidado      FLOAT,
    limite_soma_individual  FLOAT,
    intercompany_obs        TEXT
);


-- ── NOVA TABELA: grupo_documents ──────────────────────────────
-- Documentos consolidados do grupo (vale para todas as filhas)

CREATE TABLE IF NOT EXISTS grupo_documents (
    id              VARCHAR PRIMARY KEY,
    grupo_id        VARCHAR NOT NULL,
    created_at      DATETIME,
    field_key       VARCHAR NOT NULL,
    field_label     VARCHAR,
    original_name   VARCHAR NOT NULL,
    s3_key          VARCHAR,
    file_size       INTEGER,
    mime_type       VARCHAR,
    is_valid        BOOLEAN,
    validation_msg  TEXT,
    read_pct        FLOAT,
    doc_type_found  VARCHAR,
    is_required     BOOLEAN DEFAULT 0,
    FOREIGN KEY (grupo_id) REFERENCES grupos(id)
);


-- ── ÍNDICES (aceleram queries comuns) ─────────────────────────

CREATE INDEX IF NOT EXISTS idx_analyses_grupo_id        ON analyses(grupo_id);
CREATE INDEX IF NOT EXISTS idx_grupo_documents_grupo_id ON grupo_documents(grupo_id);
CREATE INDEX IF NOT EXISTS idx_grupos_client_id         ON grupos(client_id);
CREATE INDEX IF NOT EXISTS idx_grupos_vibra_id          ON grupos(vibra_id);


-- ── VERIFICAÇÃO PÓS-MIGRATION ─────────────────────────────────
-- Rode estes selects para conferir que tudo foi criado:
--
--   .schema grupos
--   .schema grupo_documents
--   PRAGMA table_info(analyses);   -- deve listar grupo_id e ordem_no_grupo

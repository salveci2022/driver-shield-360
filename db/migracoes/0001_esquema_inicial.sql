-- DRIVER-SHIELD 360 — esquema inicial (PostgreSQL 13+)
-- Somente comandos aditivos e idempotentes (CREATE ... IF NOT EXISTS).
-- Proibido nesta pasta: DROP, TRUNCATE, DELETE, ALTER ... DROP (verificado por teste).
--
-- Isolamento por motorista (anti-IDOR/BOLA): toda tabela com dado pessoal tem
-- motorista_id NOT NULL e toda consulta da aplicação filtra por ele.

-- ── Usuários e papéis ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usuarios (
    id                  BIGSERIAL PRIMARY KEY,
    login               TEXT        NOT NULL,
    senha_hash          TEXT        NOT NULL,
    papel               TEXT        NOT NULL CHECK (papel IN ('motorista', 'painel', 'admin')),
    ativo               BOOLEAN     NOT NULL DEFAULT TRUE,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    senha_alterada_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS usuarios_login_unico ON usuarios (lower(login));

CREATE TABLE IF NOT EXISTS motoristas (
    id          BIGSERIAL PRIMARY KEY,
    usuario_id  BIGINT      NOT NULL UNIQUE REFERENCES usuarios (id),
    nome        TEXT        NOT NULL CHECK (length(nome) BETWEEN 1 AND 60),
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Pessoa de confiança (papel 'painel') → quais motoristas pode acompanhar
CREATE TABLE IF NOT EXISTS painel_acessos (
    usuario_id    BIGINT      NOT NULL REFERENCES usuarios (id),
    motorista_id  BIGINT      NOT NULL REFERENCES motoristas (id),
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (usuario_id, motorista_id)
);

-- ── Dados do motorista ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contatos_confianca (
    id            BIGSERIAL PRIMARY KEY,
    motorista_id  BIGINT      NOT NULL REFERENCES motoristas (id),
    nome          TEXT        NOT NULL CHECK (length(nome) BETWEEN 1 AND 60),
    telefone      TEXT        NOT NULL CHECK (length(telefone) BETWEEN 1 AND 20),
    relacao       TEXT        NOT NULL DEFAULT 'Contato' CHECK (length(relacao) <= 30),
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (motorista_id, telefone)
);
CREATE INDEX IF NOT EXISTS contatos_motorista ON contatos_confianca (motorista_id);

CREATE TABLE IF NOT EXISTS veiculos (
    id            BIGSERIAL PRIMARY KEY,
    motorista_id  BIGINT      NOT NULL REFERENCES motoristas (id),
    placa         TEXT        NOT NULL CHECK (length(placa) BETWEEN 1 AND 10),
    modelo        TEXT        CHECK (length(modelo) <= 60),
    cor           TEXT        CHECK (length(cor) <= 30),
    ano           SMALLINT    CHECK (ano BETWEEN 1950 AND 2100),
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (motorista_id, placa)
);

CREATE TABLE IF NOT EXISTS viagens (
    id             BIGSERIAL PRIMARY KEY,
    publico_id     TEXT        NOT NULL UNIQUE,          -- id opaco usado pelo agendador
    motorista_id   BIGINT      NOT NULL REFERENCES motoristas (id),
    veiculo_id     BIGINT      REFERENCES veiculos (id),
    nome_exibido   TEXT        NOT NULL,
    minutos        INTEGER     NOT NULL CHECK (minutos BETWEEN 1 AND 1440),
    status         TEXT        NOT NULL CHECK (status IN
                     ('em_andamento', 'chegou_segura', 'cancelada', 'dead_mans_switch',
                      'cancelada_por_sos', 'interrompida')),
    inicio         TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_em      TIMESTAMPTZ,
    fim            TIMESTAMPTZ,
    lat_inicio     NUMERIC(9, 6) CHECK (lat_inicio BETWEEN -90 AND 90),
    lng_inicio     NUMERIC(9, 6) CHECK (lng_inicio BETWEEN -180 AND 180),
    origem_legado  TEXT
);
CREATE INDEX IF NOT EXISTS viagens_motorista ON viagens (motorista_id, inicio DESC);
-- No máximo UMA viagem em andamento por motorista
CREATE UNIQUE INDEX IF NOT EXISTS viagens_uma_ativa ON viagens (motorista_id) WHERE status = 'em_andamento';
-- Varredura do Dead Man's Switch
CREATE INDEX IF NOT EXISTS viagens_expiracao ON viagens (expira_em) WHERE status = 'em_andamento';
CREATE UNIQUE INDEX IF NOT EXISTS viagens_origem_legado ON viagens (motorista_id, origem_legado) WHERE origem_legado IS NOT NULL;

CREATE TABLE IF NOT EXISTS alertas (
    id             BIGSERIAL PRIMARY KEY,
    motorista_id   BIGINT      NOT NULL REFERENCES motoristas (id),
    viagem_id      BIGINT      REFERENCES viagens (id),
    tipo           TEXT        NOT NULL CHECK (tipo IN ('manual', 'dead_mans_switch', 'passageiro_suspeito')),
    status         TEXT        NOT NULL DEFAULT 'Ativo' CHECK (status IN ('Ativo', 'Resolvido')),
    nome_exibido   TEXT        NOT NULL,
    ocorrencia     TEXT        NOT NULL CHECK (length(ocorrencia) <= 400),
    lat            NUMERIC(9, 6) CHECK (lat BETWEEN -90 AND 90),
    lng            NUMERIC(9, 6) CHECK (lng BETWEEN -180 AND 180),
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolvido_em   TIMESTAMPTZ,
    resolvido_por  BIGINT      REFERENCES usuarios (id),
    arquivado_em   TIMESTAMPTZ,                          -- "Limpar" no painel arquiva (não apaga)
    origem_legado  TEXT
);
CREATE INDEX IF NOT EXISTS alertas_motorista ON alertas (motorista_id, criado_em DESC);
CREATE UNIQUE INDEX IF NOT EXISTS alertas_origem_legado ON alertas (motorista_id, origem_legado) WHERE origem_legado IS NOT NULL;

-- Detalhes estruturados de uma ocorrência (ex.: passageiro suspeito)
CREATE TABLE IF NOT EXISTS ocorrencias (
    id            BIGSERIAL PRIMARY KEY,
    motorista_id  BIGINT      NOT NULL REFERENCES motoristas (id),
    alerta_id     BIGINT      REFERENCES alertas (id),
    categoria     TEXT        NOT NULL CHECK (length(categoria) <= 40),
    descricao     TEXT        CHECK (length(descricao) <= 400),
    criada_em     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ocorrencias_motorista ON ocorrencias (motorista_id, criada_em DESC);

CREATE TABLE IF NOT EXISTS localizacoes (
    id             BIGSERIAL PRIMARY KEY,
    motorista_id   BIGINT      NOT NULL REFERENCES motoristas (id),
    viagem_id      BIGINT      REFERENCES viagens (id),
    alerta_id      BIGINT      REFERENCES alertas (id),
    lat            NUMERIC(9, 6) NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng            NUMERIC(9, 6) NOT NULL CHECK (lng BETWEEN -180 AND 180),
    origem         TEXT        NOT NULL CHECK (origem IN ('sos', 'viagem_inicio', 'dead_mans_switch', 'passageiro_suspeito', 'legado')),
    registrada_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS localizacoes_motorista ON localizacoes (motorista_id, registrada_em DESC);

-- ── Segurança ─────────────────────────────────────────────────────
-- Somente hashes: nenhum identificador de sessão ou IP em claro.
CREATE TABLE IF NOT EXISTS sessoes (
    sid_hash      TEXT        PRIMARY KEY,
    papel         TEXT        NOT NULL CHECK (papel IN ('motorista', 'painel', 'admin')),
    usuario_id    BIGINT      REFERENCES usuarios (id),
    motorista_id  BIGINT      REFERENCES motoristas (id),
    criada        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira        TIMESTAMPTZ NOT NULL,
    senha_fp      TEXT        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessoes_expira ON sessoes (expira);
CREATE INDEX IF NOT EXISTS sessoes_usuario ON sessoes (usuario_id);

CREATE TABLE IF NOT EXISTS tentativas (
    id      BIGSERIAL PRIMARY KEY,
    escopo  TEXT        NOT NULL,
    chave   TEXT        NOT NULL,
    ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tentativas_busca ON tentativas (escopo, chave, ts);

CREATE TABLE IF NOT EXISTS auditoria (
    id            BIGSERIAL PRIMARY KEY,
    ocorrido_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    acao          TEXT        NOT NULL,
    usuario_id    BIGINT,
    motorista_id  BIGINT,
    alvo          TEXT,
    ip_hash       TEXT,
    detalhes      JSONB       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS auditoria_motorista ON auditoria (motorista_id, ocorrido_em DESC);

-- ── Agendador central (lease: só um worker executa cada rodada) ───
CREATE TABLE IF NOT EXISTS tarefas_agendadas (
    nome             TEXT PRIMARY KEY,
    bloqueada_ate    TIMESTAMPTZ,
    executor         TEXT,
    ultima_execucao  TIMESTAMPTZ
);

-- ── Rastreabilidade da importação dos JSONs ───────────────────────
CREATE TABLE IF NOT EXISTS importacoes (
    id             BIGSERIAL PRIMARY KEY,
    motorista_id   BIGINT      NOT NULL REFERENCES motoristas (id),
    arquivo        TEXT        NOT NULL,
    sha256         TEXT        NOT NULL,
    registros      INTEGER     NOT NULL,
    importado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

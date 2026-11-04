-- Keel PostgreSQL 模式（v0.6 骨架）
-- 表清单与架构文档 §5.3 对应；两者不一致时以本文件为准，并回改文档。
-- 所有时间列均为 UTC（timestamptz），容器 TZ=UTC。

-- 交易对主数据。V1–V2 固定 market_type='spot'（ADR-001）
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id   TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,               -- 如 BTC/USDT
    base            TEXT NOT NULL,
    quote           TEXT NOT NULL,
    market_type     TEXT NOT NULL DEFAULT 'spot',
    status          TEXT NOT NULL DEFAULT 'TRADING',  -- TRADING / BREAK / HALT
    price_precision INT  NOT NULL DEFAULT 8,
    qty_precision   INT  NOT NULL DEFAULT 8,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 成本模型独立成表（ADR-005）。回测与实盘共用常量，不该每个策略各存一份
CREATE TABLE IF NOT EXISTS cost_models (
    cost_model_id   TEXT PRIMARY KEY,
    fee_rate        NUMERIC(10, 8) NOT NULL,
    slippage_bps    NUMERIC(10, 4) NOT NULL,
    min_notional    NUMERIC(20, 8) NOT NULL,
    funding_included BOOLEAN NOT NULL DEFAULT FALSE,  -- 现货固定 false
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 策略参数配置。params 由 params_schema 校验（§3.7）
CREATE TABLE IF NOT EXISTS strategy_configs (
    strategy_config_id TEXT PRIMARY KEY,
    strategy_id        TEXT NOT NULL,
    params             JSONB NOT NULL DEFAULT '{}'::jsonb,
    params_schema      JSONB NOT NULL,
    cost_model_id      TEXT NOT NULL REFERENCES cost_models(cost_model_id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 袖套：一个实例只属一个袖套（§3.8）
CREATE TABLE IF NOT EXISTS sleeves (
    sleeve_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'live',     -- live / virtual
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 策略实例。state 供策略读写并回放（§3.7 修订 ⑤）
CREATE TABLE IF NOT EXISTS strategy_instances (
    instance_id        TEXT PRIMARY KEY,
    strategy_config_id TEXT NOT NULL REFERENCES strategy_configs(strategy_config_id),
    sleeve_id          TEXT REFERENCES sleeves(sleeve_id),
    instrument_id      TEXT NOT NULL REFERENCES instruments(instrument_id),
    status             TEXT NOT NULL DEFAULT 'STOPPED',
    state              JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 控制面。每次变更 version+1（§2.5.4）
CREATE TABLE IF NOT EXISTS runtime_flags (
    flag_key    TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    version     BIGINT NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT,
    reason      TEXT
);

-- 控制类接口留痕（§3.11）
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    BIGSERIAL PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 事件表：seq 才是消费水位（§2.5.2）
CREATE TABLE IF NOT EXISTS events (
    seq          BIGSERIAL PRIMARY KEY,
    event_id     UUID NOT NULL UNIQUE,
    topic        TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_topic_seq ON events (topic, seq);

-- 消费位点。按 (consumer, topic) 记，注意和 events.topic 对齐
CREATE TABLE IF NOT EXISTS consumer_offsets (
    consumer  TEXT NOT NULL,
    topic     TEXT NOT NULL,
    last_seq  BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, topic)
);

-- 去重表，保留 30 天（§2.5.3）
CREATE TABLE IF NOT EXISTS consumed_events (
    consumer   TEXT NOT NULL,
    event_id   UUID NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, event_id)
);

-- 回测作业。stale 表示数据修订后结果失效，不自动重跑（§6.3）
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id        TEXT PRIMARY KEY,
    strategy_config_id TEXT,
    symbol        TEXT NOT NULL,
    interval      TEXT NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL DEFAULT 'QUEUED',  -- QUEUED/RUNNING/SUCCEEDED/FAILED
    fail_reason   TEXT,                            -- TIMEOUT/ORPHANED/ERROR
    stale         BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key TEXT UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_status ON backtest_runs (status, created_at);

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

-- 默认成本模型必须存在，否则 API-03 提交回测时 cost_model_id 指向一张空表，
-- 而 backtest_runs.cost_model_id 在 V1 只有弱约束（见文件尾部），
-- 于是「提交成功、跑起来才发现没有成本模型」——错误被推迟到最不方便的时候。
-- 这里的数值与 keel.research.runner.DEFAULT_COST 必须一致：
-- 两处不一致的表现是回测指标随「从哪条路径进来」而变。
INSERT INTO cost_models (cost_model_id, fee_rate, slippage_bps, min_notional, funding_included)
VALUES ('default', 0.001, 5.0, 5.0, FALSE)
ON CONFLICT (cost_model_id) DO NOTHING;

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

-- 回测作业补列：universe 让一次提交覆盖多个标的（§6.3 API-03）
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS universe JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS engine TEXT NOT NULL DEFAULT 'keel-bar';
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS engine_version TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS data_snapshot_id TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS metrics JSONB;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS strategy_key TEXT;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS strategy_version INTEGER DEFAULT 1;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS params JSONB;
ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS cost_model_id TEXT;

-- 批处理作业台账（§5.3）。checkpoint 存水位线，export-tx 靠它决定从哪继续
CREATE TABLE IF NOT EXISTS job_runs (
    job_run_id  BIGSERIAL PRIMARY KEY,
    job_id      TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'RUNNING',   -- RUNNING/SUCCEEDED/FAILED
    rows        BIGINT NOT NULL DEFAULT 0,
    checkpoint  JSONB,
    error       TEXT
);

-- 制品（报告、曲线、交易明细）。只存 storage_key，绝不存绝对路径（§3.11）
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,
    run_id       TEXT REFERENCES backtest_runs(run_id),
    kind         TEXT NOT NULL,                    -- report_json / trades_csv / equity_csv
    storage_key  TEXT NOT NULL,
    bytes        BIGINT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 心跳（§2.5.7）。写库不写日志：容器挂了日志也就没了
CREATE TABLE IF NOT EXISTS heartbeats (
    component TEXT PRIMARY KEY,
    beat_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 迟到事件（§2.5.3）：available_at 已过但被更晚的事件挤到后面
CREATE TABLE IF NOT EXISTS late_events (
    seq        BIGINT PRIMARY KEY,
    event_id   UUID NOT NULL,
    topic      TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 告警抑制（§2.5.7）：同 alert_key 30 分钟内只发一次
CREATE TABLE IF NOT EXISTS alert_log (
    alert_key   TEXT NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    severity    TEXT NOT NULL,
    PRIMARY KEY (alert_key, sent_at)
);

# Keel（龙骨）

个人加密货币量化平台。单用户、单机、本地优先。

当前状态：**V1（研究回测）实现完成**。数据回填 → 清洗 → 特征 → 逐 bar 回测 → 报告 →
网页查看，整条链路已在真实 Binance 数据上跑通。实盘（V2）与组合（V3）尚未开始。

## 文档

唯一的架构真相在 [docs/architecture-design.md](docs/architecture-design.md)。
V1 的分步实施计划在 [docs/superpowers/plans/2026-11-04-v1-research-backtest.md](docs/superpowers/plans/2026-11-04-v1-research-backtest.md)。
代码与文档冲突时，以能跑的那边为准，并立刻回改另一边。

## 仓库结构

```
backend/           Python 服务（API、ETL、回测、调度、实盘运行时）
  keel/
    api/           HTTP 层：只做装配与传输，不含业务规则
    contracts.py   策略契约（已实现并测试，A9 达成）
    features.py    特征计算（唯一入口，回测与实盘共用）
    data/          数据集定义、Parquet 湖、ETL、覆盖率
    market/        行情采集（WS，V2）与历史回补（REST）
    research/      回测队列、逐 bar 执行器、指标、引擎适配
    execution/     执行网关（V2）
    risk/          风控闸门（V2）
    runtime/       策略运行时（V2）
    portfolio/     组合分配（V3）
    reports/       报告生成与制品
    scheduler/     批处理任务清单（9 条，全 UTC）
    events/        事件通道（PG 表 + LISTEN/NOTIFY）
    db/            PostgreSQL 模式与迁移、DuckDB 集市
    ops/           告警、心跳
    workers/       常驻进程入口（各容器的 main）
  tests/           137 个测试（契约 40 + 领域与 API 97）
web/               Vite + React + TypeScript 前端
deploy/            docker-compose 与镜像定义
docs/              架构设计文档与实施计划
```

## 快速开始

需要 Python 3.11/3.12、Node 22+、Docker（只有 PG 用容器，其余本地跑）。

```bash
# 1. 起库并建表
make pg-up                              # postgres:16 on :5432
cd backend && UV_CACHE_DIR="$PWD/.uv-cache" uv sync && cd ..
UV_CACHE_DIR=uv uv run --project backend python -c "from keel.db.migrations import migrate; migrate('postgresql://keel:keel@localhost:5432/keel')"

# 2. 回填一段真实数据（需要能访问 Binance）
export KEEL_PG_DSN="postgresql://keel:keel@localhost:5432/keel"
make backfill

# 3. 建集市
make etl

# 4. 提交一个回测
curl -s -X POST http://127.0.0.1:8000/api/v1/backtests \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"strategy_key":"sma_cross","strategy_version":1,"universe":["BTC/USDT"],
       "interval":"1h","from":"2024-01-02T00:00:00Z","to":"2024-01-14T00:00:00Z",
       "params":{"fast":20,"slow":60},"cost_model_id":"default","engine":"keel-bar"}'

# 5. 让 worker 认领并执行（另开一个终端常驻）
cd backend && UV_CACHE_DIR="$PWD/.uv-cache" uv run python -m keel.workers.backtest_worker

# 6. 看页面
make api        # :8000
make web        # :5173  → 行情 / 回测 / 报告
```

改数据后记得重跑 `make etl`：集市是可重建的派生物，不是真相。

## 当前能跑什么

- **数据**：`plan`/`execute` 幂等回补，Parquet 按月分区，原子写。
- **ETL**：单写者（flock）重建 `dwd_bars`/`dws_features`/`ops_coverage`，临时文件改名发布。
- **覆盖率**：缺口分 `tolerated`/`blocking`，连续 2 根缺失即阻断。
- **回测**：PG 队列 + `FOR UPDATE SKIP LOCKED` 认领，逐 bar 按 `keel.contracts` 决策，
  `t+1` 开盘成交、手续费与滑点计入，指标含夏普/回撤/换手/胜率。
- **报告**：JSON 制品，带 `disclaimer`，`content_url` 不暴露服务器路径。
- **API**：API-01/02/03/04/05/10/11/12 已实现；API-09/15 保持 501（执行网关在 V2）。
- **测试**：带 PG 时 136 passed / 1 skipped；不带 PG 时 113 passed / 24 skipped（跳过的是必须真库的队列、事件与集成测试）。

## 几个不该被"优化"掉的设计

- **API 只入队，不在请求里跑回测。** 一个 30 分钟的回测能把 HTTP 服务打死。
- **风控闸门是执行网关的进程内模块，不单独成容器。** 它校验内存里的持仓快照，
  拆出去只多一次网络往返和一个新故障点。
- **实盘特征在策略进程内现算，不读 DuckDB 集市。** 集市是 ETL 批处理产物，
  滞后一个周期；且回测与实盘必须用同一套计算，否则一致性无从谈起。
- **集市可重建，原始湖不可改。** `ods_`/`dwd_`/`dws_` 都能从上游重算。
- **总开关只停新开仓，不停平仓与撤单。** 目的是"别再买"，不是"别管了"。
- **DuckDB 单写者。** 回填进程不写集市，覆盖率由 ETL 派生——两个写者必然互相覆盖。

理由与权衡都在架构文档里，每条都记了当时否掉了什么。

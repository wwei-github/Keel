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
  tests/           174 个测试（契约 40 + 领域、API、调度与验收 134）
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
export KEEL_PG_DSN="postgresql://keel:keel@localhost:5432/keel"
make migrate

# 2. 同步交易对主数据，再回填（需要能访问 Binance）
#    顺序不能反：回补要从 instruments 表读「哪些标的在交易」，
#    表是空的时候它不报错，只是补零根 K 线。
make instruments
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

## 整套跑在 Docker 里

不想装 Python/Node 时用这条路径。`make up` 会起 postgres、migrate（一次性建表）、
api、etl-worker、backtest-worker、scheduler、web 七个容器。

```bash
make env            # 从 .env.example 生成 .env（若不存在）
make up             # DOCKER_BUILDKIT=0 docker compose up -d --build
make logs

# 一次性数据准备（也可交给 scheduler 在 UTC 00:00 自己跑）
docker compose --env-file .env -f deploy/docker-compose.yml exec api \
  python -c "from keel.market.instruments import sync_instruments; print(sync_instruments())"
docker compose --env-file .env -f deploy/docker-compose.yml exec api \
  python -c "from keel.tools.backfill import run_for_enabled_instruments as r; print(len(r(lookback_days=45)))"
```

起好后：Web `http://127.0.0.1:15173`（nginx 反代 `/api/`），API 直连 `http://127.0.0.1:18000`。
**端口用 `.env` 里的 `KEEL_HOST_*_PORT` 覆盖**，因为 `${VAR}` 插值只读 compose 文件所在目录
的 `.env` 或 `--env-file`，`env_file:` 只影响容器内环境变量、不影响端口映射。

macOS 上 `docker build` 必须带 `DOCKER_BUILDKIT=0`：buildx 会去写
`~/.docker/buildx/activity/`，权限不足时报 `operation not permitted`，
报错看起来像 daemon 挂了，实际不是。`make` 里已默认 `BUILDKIT=0`。

## 当前能跑什么

- **主数据**：`sync_instruments` 从 Binance 拉现货 USDT 对（实测 518 个），下线改 `HALT` 不删行。
- **数据**：`plan`/`execute` 幂等回补，Parquet 按月分区，原子写。
- **ETL**：单写者（flock）重建 `dwd_bars`/`dws_features`/`ops_coverage`，临时文件改名发布。
- **覆盖率**：缺口分 `tolerated`/`blocking`，连续 2 根缺失即阻断。
- **验收**：`make acceptance` 跑 A1/A2/A3/A7，全部可判定、可失败。
- **回测**：PG 队列 + `FOR UPDATE SKIP LOCKED` 认领，逐 bar 按 `keel.contracts` 决策，
  `t+1` 开盘成交、手续费与滑点计入，指标含夏普/回撤/换手/胜率。
- **报告**：JSON 制品，带 `disclaimer`，`content_url` 不暴露服务器路径。
- **API**：API-01/02/03/04/05/10/11/12 已实现；API-09/15 保持 501（执行网关在 V2）。
- **测试**：带 PG 时 173 passed / 1 skipped；不带 PG 时 143 passed / 31 skipped（跳过的是必须真库的队列、事件与集成测试）。

## 几个不该被"优化"掉的设计

- **API 只入队，不在请求里跑回测。** 一个 30 分钟的回测能把 HTTP 服务打死。
- **风控闸门是执行网关的进程内模块，不单独成容器。** 它校验内存里的持仓快照，
  拆出去只多一次网络往返和一个新故障点。
- **实盘特征在策略进程内现算，不读 DuckDB 集市。** 集市是 ETL 批处理产物，
  滞后一个周期；且回测与实盘必须用同一套计算，否则一致性无从谈起。
- **集市可重建，原始湖不可改。** `ods_`/`dwd_`/`dws_` 都能从上游重算。
- **总开关只停新开仓，不停平仓与撤单。** 目的是"别再买"，不是"别管了"。
- **DuckDB 单写者。** 回填进程不写集市，覆盖率由 ETL 派生——两个写者必然互相覆盖。
- **定时任务的触发器由 cron 表达式构造，不用字符串 `"cron"`。**
  后者会建出全通配的 cron（每分钟跑一次），日志看起来完全正常。
- **原始湖只追加，重复行在读取时收敛。** 同根 bar 多份是设计内状态（revision 历史），
  不是脏数据；去重放读取侧，磁盘上留全部版本。
- **nginx 反代用变量形式的上游。** 写死主机名只解析一次，api 容器重建后 web 会一直 502。
- **特征按 (symbol, interval) 分组计算。** 整表一次算会让 `open_time` 索引撞车，
  并让滚动窗口跨标的续算——BTC 的尾部窗口会参与 ETH 的 SMA，两个标的的特征都错。

## 镜像构建

`docker build` 在本机默认走 buildx，会报
`failed to update builder last activity time: ... operation not permitted`（buildx 的
activity 目录权限问题，不是 Docker daemon 的问题）。加 `DOCKER_BUILDKIT=0` 走旧构建器即可：

```bash
DOCKER_BUILDKIT=0 docker build -f deploy/Dockerfile.backend -t keel-backend:test .
DOCKER_BUILDKIT=0 docker build -f deploy/Dockerfile.web -t keel-web:test .
```

`make up` 已经带了这个变量（`BUILDKIT ?= 0`），Linux 上想恢复默认构建器用 `make up BUILDKIT=1`。

整套跑起来：

```bash
make env && make up          # 六服务 + 一次性 migrate
docker compose exec api python -c \
  "from keel.market.instruments import sync_instruments; print(sync_instruments())"
docker compose exec api python -c \
  "from keel.tools.backfill import run_for_enabled_instruments as r; print(len(r(lookback_days=1)))"
```

`migrate` 是 compose 里的一次性服务，跑完就退出，其余服务等它 `service_completed_successfully`
才启动——少了这一步，worker 会以 `UndefinedTable: relation "backtest_runs" does not exist`
无限重启，看起来像崩溃循环，其实是库里没表。

主数据与首次回填**不在** compose 启动流程里：它们需要访问 Binance，放进启动链会让
整个栈依赖外网。所以首次部署后要手敲上面两条，否则会得到「服务都健康、一根 K 线都没有」。

注意构建上下文是**仓库根**（`deploy/docker-compose.yml` 里 `context: ..`），
Dockerfile 里的 `COPY backend/...` 是相对根的。在 `backend/` 里执行会报
`stat backend/pyproject.toml: file does not exist`。

理由与权衡都在架构文档里，每条都记了当时否掉了什么。

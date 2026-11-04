# Keel（龙骨）

个人加密货币量化平台。单用户、单机、本地优先。

当前状态：**架构设计完成（文档 v0.7），代码处于骨架阶段**。骨架意味着目录、接口形状、
配置与部署编排都已就位，但绝大多数模块是 `raise NotImplementedError`——
除策略契约与其测试外，没有任何东西被验证过。

## 文档

唯一的架构真相在 [docs/architecture-design.md](docs/architecture-design.md)。
代码与文档冲突时，以能跑的那边为准，并立刻回改另一边——
文档漂移的成本远高于改文档的十分钟。

## 仓库结构

```
backend/           Python 服务（API、ETL、回测、调度、实盘运行时）
  keel/
    api/           HTTP 层：只做装配与传输，不含业务规则
    contracts.py   策略契约（已实现并测试，A9 达成）
    features.py    特征计算（唯一入口，回测与实盘共用）
    data/          数据集定义与 ETL
    market/        行情采集（WS）与历史回补（REST）
    research/      回测作业认领与引擎适配
    execution/     执行网关（风控闸门的宿主进程）
    risk/          风控闸门（纯函数优先）
    runtime/       策略运行时
    portfolio/     组合分配（V3）
    reports/       报告生成
    scheduler/     批处理任务清单
    events/        事件通道（PG 表 + LISTEN/NOTIFY）
    db/            PostgreSQL 模式与迁移
    ops/           告警、心跳
    workers/       常驻进程入口（各容器的 main）
  tests/           契约测试（40 个，全绿）
web/               Vite + React + TypeScript 前端
  src/api/         HTTP 客户端（Envelope 解包、错误规格化）
  src/pages/       页面
deploy/            docker-compose 与镜像定义
docs/              架构设计文档
```

## 快速开始

后端需要 Python 3.11 或 3.12。

```bash
cd backend
UV_CACHE_DIR="$PWD/.uv-cache" uv sync
UV_CACHE_DIR="$PWD/.uv-cache" uv run pytest
UV_CACHE_DIR="$PWD/.uv-cache" uv run uvicorn keel.api.app:app --reload
```

前端需要 Node 22+。

```bash
cd web
npm install
npm run dev          # http://127.0.0.1:5173，/api 代理到 8000
```

## 当前能跑什么

- `pytest` — 40 个契约测试，覆盖窗口切分、状态回放、特征无未来数据、参数超窗口拒绝。
- `GET /api/v1/health` — 返回真实状态。
- 其余接口返回 `501 NOT_IMPLEMENTED`，走统一错误形状，前端可照此联调。

## 几个不该被"优化"掉的设计

- **API 只入队，不在请求里跑回测。** 一个 30 分钟的回测能把 HTTP 服务打死。
- **风控闸门是执行网关的进程内模块，不单独成容器。** 它校验内存里的持仓快照，
  拆出去只多一次网络往返和一个新故障点。
- **实盘特征在策略进程内现算，不读 DuckDB 集市。** 集市是 ETL 批处理产物，
  滞后一个周期；且回测与实盘必须用同一套计算，否则一致性无从谈起。
- **集市可重建，原始湖不可改。** `ods_` / `dwd_` / `dws_` 都能从上游重算，`ops_` 不能。
- **总开关只停新开仓，不停平仓与撤单。** 目的是"别再买"，不是"别管了"。

理由与权衡都在架构文档里，每条都记了当时否掉了什么。

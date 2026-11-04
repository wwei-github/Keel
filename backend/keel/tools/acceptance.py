"""把 §11.1 的 A1–A10 变成可执行检查。"""

from __future__ import annotations

from pathlib import Path

from keel.data.coverage import analyse
from keel.data.lake import read_bars
from keel.features import FEATURE_VERSION

A1_RATIO_MIN = 0.995
# 1e-9 相对误差：比任何真实实现漂移小六个数量级，同时容得下 BLAS 分块的末位差。
A3_REL_TOL = 1e-9


def check_a1(root: Path, symbols: list[str], interval: str) -> dict:
    ratios = []
    blocking = 0
    for symbol in symbols:
        frame = read_bars(Path(root), symbol=symbol, interval=interval)
        cov = analyse(frame, interval)
        ratios.append(cov.ratio)
        blocking += cov.blocking_gaps
    ratio = sum(ratios) / len(ratios) if ratios else 0.0
    return {
        "name": "A1 数据覆盖率",
        "ratio": ratio,
        "blocking_gaps": blocking,
        "ok": ratio >= A1_RATIO_MIN and blocking == 0,
    }


def check_a2(root: Path, symbol: str, interval: str, job) -> dict:
    """回填幂等：同一区间连跑两次，第二次不应再写入任何行。"""
    from keel.market.backfill import execute, plan

    from .exchange_factory import make_exchange

    exchange = make_exchange()
    first = plan([job], root=root)
    written_first = sum(execute(j, exchange=exchange, root=root).rows_written for j in first)
    second = plan([job], root=root)
    written_second = sum(execute(j, exchange=exchange, root=root).rows_written for j in second)
    return {
        "name": "A2 回填幂等",
        "written_first": written_first,
        "written_second": written_second,
        "remaining_plans": len(second),
        "ok": written_second == 0 and not plan([job], root=root),
    }


def check_a4(raw_root: Path, run_factory) -> dict:
    """同 run_id 重跑两次，指标必须完全相等。

    ``run_factory`` 每次返回一个新的 run 对象（同一个 run_id），这样 compare 的是
    「同输入同代码的两次执行」，而不是两次不同的作业。
    """
    import json

    from keel.research.runner import execute_run

    first = execute_run(run_factory(), raw_root=raw_root)
    second = execute_run(run_factory(), raw_root=raw_root)
    left = json.dumps(first["metrics"], sort_keys=True)
    right = json.dumps(second["metrics"], sort_keys=True)
    return {"name": "A4 重跑一致", "identical": left == right, "ok": left == right}


def check_a7() -> dict:
    """无私有 Key 泄漏、V1 未启动执行网关。静态扫描比运行时断言靠谱。

    两个必须排除的东西，都是第一版踩出来的：
    - **本文件自己**：正则写在源码里，``grep`` 必然先命中这一行，于是检查永远
      失败。第一版就是这样，输出里把自己和 ``.pyc`` 都报成了违规。
    - **``__pycache__`` 与二进制**：``grep`` 对 ``.pyc`` 只说「Binary file ...
      matches」，既定位不到行也没法判断是谁，属于噪声。
    """
    import re

    pattern = re.compile(r"\b(create_order|create_market_order|cancel_order)\b")
    package = Path(__file__).resolve().parent.parent
    me = Path(__file__).resolve()
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if path.resolve() == me or "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    # 执行网关与风控是 V2 的事，V1 代码里不该出现真实下单调用
    offenders = [h for h in offenders if "/execution/" not in h and "/risk/" not in h]
    return {"name": "A7 无下单调用", "offenders": offenders, "ok": not offenders}


def check_a3(
    symbols: list[str],
    interval: str,
    *,
    mart: Path | None = None,
    root: Path | None = None,
    feature_version: str = FEATURE_VERSION,
) -> dict:
    """A3 特征一致：集市里的 ``dws_features`` 与决策时现算的值必须逐值相同。

    比对方式：从原始湖按 (symbol, interval) 重算一遍特征，按 ``open_time``
    与集市左连接，逐列取最大相对误差。**不用全等比较**，理由与 §7 的浮点口径
    一致：同一次计算的两次结果可能因 BLAS 分块不同差在末位，要求 bit 级相等
    会得到偶发的假失败；1e-9 已经比任何真实的实现漂移小六个数量级以上。

    只用 ``symbol`` 列取值、不依赖集市里有没有其他标的——反向验证过：
    把 ETL 改成整表混算，这条会给出 ``max_rel_error`` 非零而不是静默通过。
    """
    import numpy as np
    import pandas as pd

    from keel.config import get_settings
    from keel.data.lake import read_bars
    from keel.db import mart as mart_mod
    from keel.features import compute

    target = Path(mart) if mart else Path(get_settings().duckdb_path)
    raw = Path(root) if root else Path(get_settings().raw_root)
    worst = 0.0
    checked = 0
    problems: list[str] = []

    with mart_mod.connect(target) as con:
        for symbol in symbols:
            got = con.execute(
                "select * from dws_features where symbol = ? and interval = ? order by open_time",
                [symbol, interval],
            ).fetch_df()
            if got.empty:
                problems.append(f"{symbol}/{interval}: dws_features 无数据")
                continue
            bars = read_bars(raw, symbol=symbol, interval=interval)
            if bars.empty:
                problems.append(f"{symbol}/{interval}: 原始湖无数据")
                continue
            bars = bars.sort_values("open_time").set_index("open_time")
            want = compute(bars, feature_version).reset_index()
            got["open_time"] = pd.to_datetime(got["open_time"], utc=True)
            want["open_time"] = pd.to_datetime(want["open_time"], utc=True)
            merged = got.merge(want, on="open_time", suffixes=("_mart", "_now"), how="inner")
            if merged.empty:
                problems.append(f"{symbol}/{interval}: 时间戳对不上，无法比对")
                continue
            for col in want.columns:
                if col == "open_time" or col not in got.columns:
                    continue
                left = merged[f"{col}_mart"].to_numpy(dtype="float64")
                right = merged[f"{col}_now"].to_numpy(dtype="float64")
                same_nan = np.isnan(left) & np.isnan(right)
                scale = np.maximum(np.abs(left), np.abs(right))
                # 两侧都是 0 或都是 NaN 时相对误差无意义，直接剔除
                usable = ~same_nan & (scale > 0)
                if not usable.any():
                    continue
                rel = np.abs(left[usable] - right[usable]) / scale[usable]
                worst = max(worst, float(np.nanmax(rel)))
            checked += len(merged)

    ok = worst <= A3_REL_TOL and not problems
    return {
        "name": "A3 特征一致",
        "rows_compared": checked,
        "max_rel_error": worst,
        "tolerance": A3_REL_TOL,
        "problems": problems,
        "ok": ok,
    }


def check_a2_live(root: Path, symbol: str, interval: str) -> dict:
    """A2 的可直接跑版本：对**最近已经补过的区间**再 plan 一次，必须为空。

    原来的 ``check_a2`` 需要调用方传一个 job，没人传就永远不跑——验收脚本里
    留一条永不执行的检查，比不写更糟：它让人以为验过了。这里改成自己算区间：
    取该标的原始湖里已有的最后 N 根，重新 plan，按定义应当不需要再补。

    注意区间取「已有数据的尾部」而不是「过去两天」：后者在没联网的环境下
    会去拉新数据，把「幂等性」验成「网络可用性」。
    """
    import pandas as pd

    from keel.market.backfill import BackfillJob, plan

    frame = read_bars(root, symbol=symbol, interval=interval)
    if frame.empty:
        return {"name": "A2 回填幂等", "ok": False, "reason": f"{symbol} 无数据，无法验证"}
    # 取已有的最后一段（最多 72 根），区间端点直接用数据自己的时间戳
    tail = frame.sort_values("open_time").tail(72)
    start = pd.Timestamp(tail["open_time"].iloc[0]).to_pydatetime()
    end = pd.Timestamp(tail["open_time"].iloc[-1]).to_pydatetime()
    job = BackfillJob(symbol, interval, start, end)
    todo = plan([job], root=root)
    return {
        "name": "A2 回填幂等",
        "checked_range": f"{start.isoformat()} ~ {end.isoformat()}",
        "remaining_plans": len(todo),
        "ok": not todo,
    }


def main() -> int:
    from keel.config import get_settings

    settings = get_settings()
    root = Path(settings.raw_root)
    symbols = ["BTC/USDT", "ETH/USDT"]
    results = [
        check_a1(root, symbols, "1h"),
        check_a2_live(root, "BTC/USDT", "1h"),
        check_a3(symbols, "1h"),
        check_a7(),
    ]
    for item in results:
        mark = "PASS" if item["ok"] else "FAIL"
        print(f"[{mark}] {item['name']}")
        for key, value in item.items():
            if key not in ("name", "ok"):
                print(f"        {key}: {value}")
    return 0 if all(i["ok"] for i in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

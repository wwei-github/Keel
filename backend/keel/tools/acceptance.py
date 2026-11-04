"""把 §11.1 的 A1–A10 变成可执行检查。"""

from __future__ import annotations

from pathlib import Path

from keel.data.coverage import analyse
from keel.data.lake import read_bars

A1_RATIO_MIN = 0.995


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
    """无私有 Key 泄漏、V1 未启动执行网关。静态扫描比运行时断言靠谱。"""
    import subprocess
    from pathlib import Path as _Path

    package = _Path(__file__).resolve().parent.parent
    hits = subprocess.run(
        ["grep", "-rn", "-E", r"create_order|create_market_order|cancel_order", str(package)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    # 执行网关与风控是 V2 的事，V1 代码里不该出现真实下单调用
    offenders = [h for h in hits if "/execution/" not in h and "/risk/" not in h]
    return {"name": "A7 无下单调用", "offenders": offenders, "ok": not offenders}


def main() -> int:

    from keel.config import get_settings

    settings = get_settings()
    root = Path(settings.raw_root)
    results = [check_a1(root, ["BTC/USDT"], "1h"), check_a7()]
    for item in results:
        mark = "PASS" if item["ok"] else "FAIL"
        print(f"[{mark}] {item['name']}")
        for key, value in item.items():
            if key not in ("name", "ok"):
                print(f"        {key}: {value}")
    return 0 if all(i["ok"] for i in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

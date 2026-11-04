"""验收检查项本身要能被测。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.market.normalize import normalize_ohlcv
from keel.tools.acceptance import check_a1

HOUR = 3_600_000


def _seed(root: Path, gaps: list[int], symbol: str = "BTC/USDT", n: int = 10) -> None:
    rows = []
    for i in range(n):
        if i in gaps:
            continue
        # 每个标的给一套独立的、同形的价格：同形才能验证「两标的特征应当相同」，
        # 独立才不会与别的测试互相干扰
        base = 100.0 + (1.0 if symbol.startswith("ETH") else 0.0)
        rows.append(
            [1_699_200_000_000 + i * HOUR, base + i, base + 1 + i, base - 1 + i, base + i, 1.0]
        )
    frame = normalize_ohlcv(rows, symbol, "1h")
    write_partition(
        partition_dir(root, ODS_BARS, symbol, "1h", datetime(2023, 11, 1, tzinfo=UTC)), frame
    )


def test_a1_fails_on_missing_bar(tmp_path: Path) -> None:
    _seed(tmp_path, gaps=[4])
    out = check_a1(tmp_path, ["BTC/USDT"], "1h")
    assert out["blocking_gaps"] == 0 and out["ratio"] < 1.0
    assert out["ok"] is False  # A1 要求覆盖率 ≥ 99.5% 且 blocking 为 0


def test_a1_passes_on_complete_data(tmp_path: Path) -> None:
    _seed(tmp_path, gaps=[])
    out = check_a1(tmp_path, ["BTC/USDT"], "1h")
    assert out["ok"] is True


def test_a1_flags_blocking_gap(tmp_path: Path) -> None:
    _seed(tmp_path, gaps=[4, 5, 6])
    out = check_a1(tmp_path, ["BTC/USDT"], "1h")
    assert out["blocking_gaps"] >= 1 and out["ok"] is False


def _seed_mart(tmp_path: Path, symbols: list[str]) -> Path:
    from keel.data import etl

    for symbol in symbols:
        # 30 根：sma_20 需要 20 根才有值，否则全 NaN、篡改也看不出来
        _seed(tmp_path / "raw", gaps=[], symbol=symbol, n=30)
    target = tmp_path / "keel.duckdb"
    etl.run_once(root=tmp_path / "raw", mart=target)
    return target


def test_a3_passes_when_mart_matches_recompute(tmp_path: Path) -> None:
    from keel.tools.acceptance import check_a3

    target = _seed_mart(tmp_path, ["BTC/USDT", "ETH/USDT"])
    out = check_a3(["BTC/USDT", "ETH/USDT"], "1h", mart=target, root=tmp_path / "raw")
    assert out["ok"] is True
    assert out["rows_compared"] == 60
    assert out["max_rel_error"] == 0.0


def test_a3_fails_when_mart_is_tampered(tmp_path: Path) -> None:
    """验收项必须能失败。改一行特征值，A3 要报出来。"""
    import duckdb

    from keel.tools.acceptance import check_a3

    target = _seed_mart(tmp_path, ["BTC/USDT"])
    con = duckdb.connect(str(target))
    try:
        con.execute("update dws_features set sma_20 = sma_20 * 1.001 where sma_20 is not null")
    finally:
        con.close()
    out = check_a3(["BTC/USDT"], "1h", mart=target, root=tmp_path / "raw")
    assert out["ok"] is False
    # 改 0.1% 的相对误差是 |1-1/1.001| ≈ 9.99e-4，不是 1e-3
    assert out["max_rel_error"] > 5e-4


def test_a3_reports_missing_symbol_instead_of_passing(tmp_path: Path) -> None:
    """集市里没有这个标的时不能静默通过——那正是「看起来绿了」的典型。"""
    from keel.tools.acceptance import check_a3

    target = _seed_mart(tmp_path, ["BTC/USDT"])
    out = check_a3(["BTC/USDT", "SOL/USDT"], "1h", mart=target, root=tmp_path / "raw")
    assert out["ok"] is False
    assert any("SOL/USDT" in p for p in out["problems"])


def test_a7_ignores_itself(tmp_path: Path) -> None:
    """A7 曾经把自己扫成违规（正则写在源码里），于是永远失败。"""
    from keel.tools.acceptance import check_a7

    out = check_a7()
    assert out["ok"] is True, out["offenders"]
    assert not any("acceptance.py" in o for o in out["offenders"])


def test_a2_live_detects_missing_tail(tmp_path: Path) -> None:
    """补过的区间再 plan 应为空；删掉尾巴后应能识别出来。"""
    import shutil

    from keel.tools.acceptance import check_a2_live

    _seed(tmp_path, gaps=[])
    out = check_a2_live(tmp_path, "BTC/USDT", "1h")
    assert out["ok"] is True
    assert out["remaining_plans"] == 0

    shutil.rmtree(tmp_path)
    _seed(tmp_path, gaps=[])

"""验收检查项本身要能被测。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from keel.data.datasets import ODS_BARS
from keel.data.lake import partition_dir, write_partition
from keel.market.normalize import normalize_ohlcv
from keel.tools.acceptance import check_a1

HOUR = 3_600_000


def _seed(root: Path, gaps: list[int]) -> None:
    rows = []
    for i in range(10):
        if i in gaps:
            continue
        rows.append([1_699_200_000_000 + i * HOUR, 100.0, 101.0, 99.0, 100.0, 1.0])
    frame = normalize_ohlcv(rows, "BTC/USDT", "1h")
    write_partition(
        partition_dir(root, ODS_BARS, "BTC/USDT", "1h", datetime(2023, 11, 1, tzinfo=UTC)), frame
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

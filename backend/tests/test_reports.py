"""报告指标与制品落地。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from keel.reports import artifacts, quantstats


def _returns(n: int = 400, mu: float = 0.0004) -> pd.Series:
    idx = pd.date_range("2023-01-01", periods=n, freq="1D", tz="UTC")
    rng = np.random.default_rng(7)
    return pd.Series(rng.normal(mu, 0.01, n), index=idx)


def test_report_has_required_keys() -> None:
    rep = quantstats.build_report(_returns(), "1d")
    assert {"cagr", "sharpe", "sortino", "max_drawdown", "calmar", "volatility", "curve"} <= set(
        rep
    )
    assert len(rep["curve"]) == 400


def test_report_marks_simulated() -> None:
    rep = quantstats.build_report(_returns(), "1d")
    # A8：报告必须自带"模拟回测"标记，不能靠前端补
    assert rep["disclaimer"] == "模拟回测，非实盘业绩"


def test_build_report_rejects_unsupported_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        quantstats.build_report(_returns(), "7m")


def test_artifact_roundtrip(tmp_path: Path) -> None:
    artifact_id = artifacts.write_artifact("report_json", {"a": 1}, root=tmp_path)
    path = artifacts.resolve(artifact_id, root=tmp_path)
    assert json.loads(path.read_text())["a"] == 1
    assert artifacts.content_url(artifact_id) == f"/api/v1/artifacts/{artifact_id}"


def test_artifact_id_is_not_a_path(tmp_path: Path) -> None:
    artifact_id = artifacts.write_artifact("report_json", {"a": 1}, root=tmp_path)
    assert "/" not in artifact_id and ".." not in artifact_id

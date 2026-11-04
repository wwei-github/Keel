"""API-01/02：形状、降采样语义、stale 标记。"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from keel.api.app import create_app
from keel.db import mart

HOUR = 3_600_000


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    target = tmp_path / "keel.duckdb"

    def build(path: Path) -> None:
        con = duckdb.connect(str(path))
        con.execute(
            """
            create table dwd_bars as
            select * from (values
              (timestamptz '2023-11-01 00:00:00+00', 'BTC/USDT', '1h',
               1.0, 2.0, 0.5, 1.5, 10.0, 1, false),
              (timestamptz '2023-11-01 01:00:00+00', 'BTC/USDT', '1h',
               1.5, 3.0, 1.4, 2.5, 20.0, 1, false),
              (timestamptz '2023-11-01 03:00:00+00', 'BTC/USDT', '1h',
               2.5, 4.0, 2.0, 3.5, 30.0, 1, false)
            ) as t(open_time, symbol, interval, open, high, low, close, volume,
                   revision, is_outlier)
            """
        )
        con.execute(
            """
            create table ops_coverage as
            select 'BTC/USDT' as symbol, '1h' as interval,
                   timestamptz '2023-11-01 00:00:00+00' as start,
                   timestamptz '2023-11-01 03:00:00+00' as "end",
                   4 as expected, 3 as present, 0 as blocking_gaps,
                   '[{"missing": 1, "severity": "tolerated"}]' as gaps
            """
        )
        con.close()

    mart.publish(build, target)
    monkeypatch.setenv("KEEL_DUCKDB_PATH", str(target))
    from keel.config import get_settings

    get_settings.cache_clear()
    return TestClient(create_app())


def test_bars_returns_envelope(client: TestClient) -> None:
    r = client.get(
        "/api/v1/bars",
        params={
            "symbol": "BTC/USDT",
            "interval": "1h",
            "from": "2023-11-01T00:00:00Z",
            "to": "2023-11-02T00:00:00Z",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3
    assert body["data"][0]["close"] == 1.5
    assert body["meta"]["as_of"].startswith("2023-11-01T03:00:00")


def test_bars_max_points_downsamples(client: TestClient) -> None:
    r = client.get(
        "/api/v1/bars",
        params={
            "symbol": "BTC/USDT",
            "interval": "1h",
            "from": "2023-11-01T00:00:00Z",
            "to": "2023-11-02T00:00:00Z",
            "max_points": 2,
        },
    )
    data = r.json()["data"]
    # 3 根凑 2 个点 → 桶宽 = ceil(3/2) × 1h = 2h，得到 00:00 与 02:00 两个桶
    assert len(data) == 2
    # open=桶内首值 high=max low=min close=末值 volume=sum
    assert data[0]["open"] == 1.0
    assert data[0]["high"] == 3.0
    assert data[0]["low"] == 0.5
    assert data[0]["close"] == 2.5
    assert data[0]["volume"] == 30.0


def test_bars_unknown_symbol_is_empty_not_error(client: TestClient) -> None:
    r = client.get(
        "/api/v1/bars",
        params={
            "symbol": "NOPE/USDT",
            "interval": "1h",
            "from": "2023-11-01T00:00:00Z",
            "to": "2023-11-02T00:00:00Z",
        },
    )
    assert r.status_code == 200 and r.json()["data"] == []


def test_coverage_returns_gaps(client: TestClient) -> None:
    r = client.get("/api/v1/coverage", params={"symbol": "BTC/USDT", "interval": "1h"})
    body = r.json()
    assert body["data"]["present"] == 3
    assert body["data"]["gaps"][0]["severity"] == "tolerated"


def test_coverage_times_are_utc(client: TestClient) -> None:
    """DuckDB 按会话时区返回 timestamptz，接口必须转成 UTC。

    不转的话，同一份数据在东八区机器上返回 ``+08:00``、在 UTC 机器上返回 ``Z``，
    前端按 UTC 解析就会整体偏 8 小时——而这种差异在本机测试里看不出来。
    """
    r = client.get("/api/v1/coverage", params={"symbol": "BTC/USDT", "interval": "1h"})
    start = r.json()["data"]["start"]
    assert start.endswith("Z") or start.endswith("+00:00")

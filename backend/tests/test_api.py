"""API 骨架的冒烟测试。

骨架阶段唯一有意义的测试：**接口形状**。形状错了改起来比加实现贵得多，
所以先用测试把它钉住，实现回来填空时不用重新想协议。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from keel.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["status"] == "ok"
    # 成功响应必须带 meta，前端靠它读 stale
    assert "meta" in body and "stale" in body["meta"]


def test_unimplemented_endpoints_use_unified_error_shape(client: TestClient) -> None:
    # /bars 与 /reports 都已实现（Task 16/17），501 断言换到 V1 范围外的
    # 总开关接口——执行网关在 V1 不启用，API-09/15 保持占位是有意的。
    r = client.get("/api/v1/ops/kill-switch")
    assert r.status_code == 501
    err = r.json()["error"]
    assert err["code"] == "NOT_IMPLEMENTED"
    assert set(err) == {"code", "message", "details"}


def test_backtest_submit_requires_cost_model(client: TestClient) -> None:
    """缺少 cost_model_id 应当是 422 参数错误，而不是 500。

    这条测试守的是"成本模型不是可选参数"这个决定（ADR-005）。
    """
    r = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": "sma_cross",
            "symbol": "BTC/USDT",
            "interval": "1h",
            "start": "2024-01-01",
            "end": "2025-01-01",
        },
    )
    assert r.status_code == 422


def test_unknown_route_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/nope").status_code == 404

"""测试公共夹具。需要真库的测试一律走 `KEEL_TEST_PG_DSN`，没设就 skip。"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get("KEEL_TEST_PG_DSN", "")
    if not dsn:
        pytest.skip("KEEL_TEST_PG_DSN 未设置；先 `make pg-up` 并 export KEEL_TEST_PG_DSN")
    return dsn

"""路由共用的依赖：认证、分页参数。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Query

from keel.config import get_settings
from keel.errors import ErrorCode, KeelError

API_PREFIX = "/api/v1"


class Unauthorized(KeelError):
    code = ErrorCode.UNAUTHORIZED
    http_status = 401


def require_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """本地单用户 Bearer Token（§6.1）。

    未配置 ``KEEL_API_TOKEN`` 时视为开发模式放行——否则本地起服务还要先想一个
    token，第一天就没人跑得起来。生产部署必须配置。
    """
    expected = get_settings().api_token
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise Unauthorized("missing or invalid bearer token")


Limit = Annotated[int, Query(ge=1, le=1000, description="单页条数（列表接口）")]
Cursor = Annotated[str | None, Query(description="游标；为空表示从头")]

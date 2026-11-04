"""运维控制接口（API-09 总开关、API-15 状态查询）。

控制类接口与查询类接口的区别不只是写库：它们要求 Bearer Token、二次确认、
``audit_log`` 留痕，并限速 10 次/分钟（§3.11、§6.1）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from keel.api.deps import API_PREFIX, require_token
from keel.api.schemas import Envelope, KillSwitchState
from keel.errors import NotImplementedYet

router = APIRouter(prefix=API_PREFIX, tags=["ops"], dependencies=[Depends(require_token)])


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = Field(min_length=1, description="必填：事后复盘靠它，不能为空")


@router.post("/ops/kill-switch")
def set_kill_switch(body: KillSwitchRequest) -> Envelope[KillSwitchState]:
    """API-09：写 ``runtime_flags``、``audit_log``、发 ``control.changed.v1``。

    ``active=true`` 表示**停止新开仓**；平仓与撤单路径不受约束——
    总开关的目的是「别再买」，不是「别管了」（§2.5.4）。
    """
    raise NotImplementedYet("API-09 set_kill_switch 尚未实现")


@router.get("/ops/kill-switch")
def get_kill_switch() -> Envelope[KillSwitchState]:
    """API-15：读当前状态与版本号。"""
    raise NotImplementedYet("API-15 get_kill_switch 尚未实现")

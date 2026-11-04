"""报告与制品接口（API-10 报告、API-12 制品下载）。

报告是制品，不是接口的产物：生成一次、存 ``artifact_root``、按 id 取。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from keel.api.deps import API_PREFIX, require_token
from keel.api.schemas import Envelope
from keel.errors import NotImplementedYet

router = APIRouter(prefix=API_PREFIX, tags=["reports"], dependencies=[Depends(require_token)])


@router.get("/reports/{run_id}")
def get_report(run_id: str) -> Envelope[dict]:
    """API-10：返回报告数据（指标、曲线序列）。

    注意返回的是**数据**，不是 HTML——渲染交给前端，
    这样同一份数据能出网页报告、也能出 PDF。
    """
    raise NotImplementedYet("API-10 get_report 尚未实现")


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> Envelope[dict]:
    """API-12：返回 ``content_url``，绝不返回服务器文件路径。

    返回绝对路径等于把内部目录结构写进公开协议，换存储就要破坏接口（§3.11）。
    """
    raise NotImplementedYet("API-12 get_artifact 尚未实现")

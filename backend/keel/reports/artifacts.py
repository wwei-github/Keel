"""制品库。文件放磁盘，元数据放 PG；接口只暴露 ``content_url``。

绝不返回绝对路径：那等于把部署细节写进协议，换存储就要破接口（§3.11）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path


def _root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    from keel.config import get_settings

    return Path(get_settings().artifact_root)


def write_artifact(
    kind: str, payload: dict, *, run_id: str | None = None, root: Path | None = None
) -> str:
    artifact_id = uuid.uuid4().hex
    directory = _root(root) / artifact_id[:2]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact_id}.{kind}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return artifact_id


def resolve(artifact_id: str, *, root: Path | None = None) -> Path:
    if "/" in artifact_id or ".." in artifact_id:
        from keel.errors import NotFound

        raise NotFound(f"bad artifact id: {artifact_id}")
    directory = _root(root) / artifact_id[:2]
    matches = sorted(directory.glob(f"{artifact_id}.*.json"))
    if not matches:
        from keel.errors import NotFound

        raise NotFound(f"artifact not found: {artifact_id}")
    return matches[0]


def content_url(artifact_id: str) -> str:
    return f"/api/v1/artifacts/{artifact_id}"


def latest_for_run(run_id: str) -> str | None:
    """某条回测最新的一份制品 id。

    制品元数据本该在 PG 的 ``artifacts`` 表里（§5.3），V1 还没建那套写入路径，
    所以先在文件系统里按 ``run_id`` 扫。退化方案写在函数里而不是散在调用点，
    将来补上 PG 只需改这一个函数。
    """
    root = _root(None)
    if not root.exists():
        return None
    for path in sorted(root.glob("*/*.report.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("run_id") == run_id:
            return path.name.split(".", 1)[0]
    return None

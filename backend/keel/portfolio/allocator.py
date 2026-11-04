"""组合分配（V3，skfolio）。§3.8、§4.3。

自动优化只产生 ``PENDING_REVIEW`` 的候选权重，必须人工 ``POST /apply`` 才生效。
理由很简单：单人系统没人守着自动换手，一次错误的再平衡能把半年收益换掉。
"算出来"和"用上去"分开，是为了让后者有一个人的判断。
"""

from __future__ import annotations


def propose(total_equity: float, sleeves: list[dict]) -> dict:
    """产出候选权重，状态 ``PENDING_REVIEW``。"""
    raise NotImplementedError("组合分配尚未实现：V3")


def apply(proposal_id: str, approved_by: str) -> dict:
    """人工确认后写入 ``allocation_weights`` 并发 ``allocation.applied.v1``。"""
    raise NotImplementedError("组合分配尚未实现：V3")

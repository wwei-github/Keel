"""调度器注册。

这里测的不是"任务能不能跑"，而是"cron 表达式有没有真的传下去"。
后者失效时系统看起来完全正常：调度器启动成功、任务每分钟都执行、
日志里每行都写着 `next run at: <now + 1min>` —— 只有把十个任务的
下次触发时间放在一起看，才会发现它们全挤在同一分钟。
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from keel.scheduler.jobs import JOBS, register


class _Recorder:
    """只记录 add_job 的调用，不启动调度器。

    用真调度器会让测试依赖 APScheduler 的线程与时钟；这里要验的是
    「注册时传了什么」，而这件事在 add_job 那一刻就已经定了。
    """

    def __init__(self) -> None:
        self.jobs: list = []

    def add_job(self, func, **kwargs):
        from types import SimpleNamespace

        job = SimpleNamespace(func=func, **kwargs)
        self.jobs.append(job)
        return job


@pytest.fixture()
def recorder() -> _Recorder:
    rec = _Recorder()
    register(rec)
    return rec


def test_every_job_is_registered(recorder: _Recorder) -> None:
    assert [j.id for j in recorder.jobs] == [j.job_id for j in JOBS]


@pytest.mark.parametrize("job", JOBS, ids=[j.job_id for j in JOBS])
def test_trigger_matches_declared_cron(recorder: _Recorder, job) -> None:
    """每个任务的触发器必须等于它自己声明的 cron。

    丢表达式的写法会得到 `cron[]`（十个字段全是 `*`），它与任何真实
    表达式都不相等，所以这条断言能直接抓住它。
    """
    expected = CronTrigger.from_crontab(job.cron, timezone=ZoneInfo("UTC"))
    actual = next(j.trigger for j in recorder.jobs if j.id == job.job_id)
    assert str(actual) == str(expected), f"{job.job_id} 的 cron 没有生效"


def test_jobs_do_not_all_fire_in_the_same_minute(recorder: _Recorder) -> None:
    """十个任务同时触发是「cron 丢失」的现场特征。

    不是所有任务都必须错开（`*/5` 与整点任务天然会碰），但全部相同说明
    表达式根本没被使用。
    """
    when = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    minutes = {j.trigger.get_next_fire_time(None, when).minute for j in recorder.jobs}
    assert len(minutes) > 1, "所有任务落在同一分钟，cron 表达式很可能没有生效"


def test_nightly_jobs_are_ordered(recorder: _Recorder) -> None:
    """主数据同步必须早于回补，回补必须早于建集市。

    顺序颠倒不会报错，只是当天补不到 K 线、或者集市建在补数据之前。
    """
    order = ["sync-instruments", "daily-backfill", "build-marts", "coverage-check"]
    when = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)
    minutes = []
    for job_id in order:
        fire = next(j.trigger for j in recorder.jobs if j.id == job_id).get_next_fire_time(
            None, when
        )
        minutes.append(fire.hour * 60 + fire.minute)
    assert minutes == sorted(minutes), f"夜间任务顺序不对: {dict(zip(order, minutes, strict=True))}"


def test_dispatch_is_bound_with_job_id(recorder: _Recorder) -> None:
    """每个注册项的 kwargs 必须带上自己的 job_id。

    漏了 kwargs 时 APScheduler 会以「缺少必需参数」为由在触发时报错，
    而注册阶段一切正常——又是一个被延迟到运行时才暴露的错误。
    """
    for job in recorder.jobs:
        assert job.kwargs == {"job_id": job.id}

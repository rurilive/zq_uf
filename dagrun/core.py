"""基于入度的 DAG 依赖调度器（仅依赖 Python 标准库）。

任务声明：
- 直接给可调用对象：无依赖任务，等价于 ``Task(fn)``；
- :class:`Task` 实例：通过 ``deps`` 声明上游任务名、``timeout`` 声明超时。

调度规则：
- 按入度调度，入度为 0 的就绪任务交给大小为 ``workers`` 的线程池并行执行；
- 上游失败后，其全部下游任务标记为 ``skipped``（跳过），而不是失败；
- 每次运行返回每个任务的 :class:`TaskRecord`（状态、产物、起止时间、跳过原因）；
- 重跑时传入上一次结果，已成功的任务直接复用产物，只补跑失败和跳过的任务。
"""

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional


#: 单个任务允许的超时上限（秒），任务声明的 timeout 会被截断到该值。
MAX_TASK_TIMEOUT = 300.0


class TaskStatus(str, Enum):
    """任务状态机：

    ``pending -> running -> success | failed``，以及
    ``pending -> skipped``（上游失败或被跳过时直接传导）。
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Task:
    """任务声明：零参数可调用对象、依赖列表和可选超时（秒）。"""

    fn: Callable[[], Any]
    deps: Iterable[str] = ()
    timeout: Optional[float] = None


@dataclass
class TaskRecord:
    """单个任务一次运行的完整记录。"""

    name: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[BaseException] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    skip_reason: Optional[str] = None
    timeout: Optional[float] = None

    @property
    def duration(self) -> Optional[float]:
        """实际执行耗时（秒）；未执行或被跳过的任务返回 ``None``。"""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class RunResult(dict):
    """运行结果。

    字典部分保持旧接口：``任务名 -> 成功产物``（失败/跳过任务不在其中）；
    完整状态通过 :meth:`records`、:meth:`record`、:meth:`status` 查询。
    """

    def __init__(self, records: Mapping[str, TaskRecord]):
        super().__init__(
            (name, record.result)
            for name, record in records.items()
            if record.status == TaskStatus.SUCCESS
        )
        self._records = dict(records)

    def records(self) -> Dict[str, TaskRecord]:
        return dict(self._records)

    def record(self, name: str) -> TaskRecord:
        return self._records[name]

    def status(self, name: str) -> TaskStatus:
        return self._records[name].status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_graph(dag):
    """归一化 DAG，校验依赖与环，返回 ``(graph, successors)``。"""
    graph: Dict[str, tuple] = {}
    for name, spec in dag.items():
        if isinstance(spec, Task):
            fn, deps, timeout = spec.fn, spec.deps, spec.timeout
        elif callable(spec):
            fn, deps, timeout = spec, (), None
        else:
            raise TypeError(f"任务 {name!r} 必须是可调用对象或 Task 实例")
        if not callable(fn):
            raise TypeError(f"任务 {name!r} 的 fn 必须是可调用对象")
        graph[name] = (fn, tuple(dict.fromkeys(deps)), timeout)

    successors: Dict[str, list] = {name: [] for name in graph}
    indegree = {}
    for name, (_, deps, _) in graph.items():
        for dep in deps:
            if dep not in graph:
                raise KeyError(f"任务 {name!r} 依赖了不存在的任务 {dep!r}")
            successors[dep].append(name)
        indegree[name] = len(deps)

    ready = [name for name, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if visited != len(graph):
        raise ValueError("DAG 中存在循环依赖，无法调度")
    return graph, successors


def run(dag, workers=1, *, resume=None, max_timeout=MAX_TASK_TIMEOUT):
    """调度执行 DAG。

    参数：
        dag: ``任务名 -> 可调用对象 | Task`` 的有序字典；
        workers: 并发线程数，必须为正整数；
        resume: 上一次运行的 :class:`RunResult` 或任务记录字典，
            其中 ``success`` 的任务本次直接复用产物，不再执行；
        max_timeout: 任务超时上限（秒），任务声明的 timeout 会被截断。

    返回：
        :class:`RunResult`，字典视图为成功任务的产物，另含全部任务记录。
    """
    if not isinstance(workers, int) or workers < 1:
        raise ValueError("workers 必须是正整数")
    if max_timeout <= 0:
        raise ValueError("max_timeout 必须为正数")

    graph, successors = _build_graph(dag)

    previous = {}
    if resume is not None:
        previous = resume.records() if isinstance(resume, RunResult) else dict(resume)

    records: Dict[str, TaskRecord] = {}
    indegree = {}
    for name, (_, deps, _) in graph.items():
        prior = previous.get(name)
        if prior is not None and prior.status == TaskStatus.SUCCESS:
            records[name] = prior
            indegree[name] = 0
        else:
            records[name] = TaskRecord(name=name)
            indegree[name] = len(deps)

    # 续跑时已成功的任务视为已完成，提前释放下游入度。
    for name in records:
        if records[name].status == TaskStatus.SUCCESS:
            for successor in successors[name]:
                indegree[successor] -= 1

    ready = [
        name
        for name in graph
        if records[name].status == TaskStatus.PENDING and indegree[name] == 0
    ]
    running: Dict[Any, tuple] = {}
    pool = ThreadPoolExecutor(max_workers=workers)

    def finish_success(name, value):
        record = records[name]
        record.status = TaskStatus.SUCCESS
        record.result = value
        record.finished_at = _now()
        for successor in successors[name]:
            indegree[successor] -= 1
            if (
                indegree[successor] == 0
                and records[successor].status == TaskStatus.PENDING
            ):
                ready.append(successor)

    def propagate_skip(name, reason):
        seen = set()
        pending_downstream = list(successors[name])
        while pending_downstream:
            current = pending_downstream.pop(0)
            if current in seen:
                continue
            seen.add(current)
            record = records[current]
            if record.status == TaskStatus.PENDING:
                record.status = TaskStatus.SKIPPED
                record.skip_reason = reason
                record.finished_at = _now()
            pending_downstream.extend(successors[current])

    def finish_failure(name, error):
        record = records[name]
        record.status = TaskStatus.FAILED
        record.error = error
        record.finished_at = _now()
        reason = (
            f"上游任务 {name!r} 失败："
            f"{type(error).__name__}: {error}"
        )
        propagate_skip(name, reason)

    try:
        while ready or running:
            while ready and len(running) < workers:
                name = ready.pop(0)
                fn, _, requested_timeout = graph[name]
                timeout = (
                    requested_timeout
                    if requested_timeout is None
                    else min(requested_timeout, max_timeout)
                )
                if timeout is None:
                    timeout = max_timeout
                record = records[name]
                record.status = TaskStatus.RUNNING
                record.timeout = timeout
                record.started_at = _now()
                future = pool.submit(fn)
                running[future] = (name, record.started_at.timestamp() + timeout)

            if not running:
                break

            nearest_deadline = min(deadline for _, deadline in running.values())
            done, _ = wait(
                tuple(running),
                timeout=max(0.0, nearest_deadline - _now().timestamp()),
            )
            if not done:
                for future, (name, deadline) in list(running.items()):
                    if not future.done() and _now().timestamp() >= deadline:
                        del running[future]
                        finish_failure(
                            name,
                            TimeoutError(
                                f"任务 {name!r} 执行超过超时上限"
                                f" {records[name].timeout} 秒"
                            ),
                        )
                continue

            for future in done:
                name, _ = running.pop(future)
                try:
                    finish_success(name, future.result())
                except BaseException as error:  # 任务抛出的任何异常都按失败处理
                    finish_failure(name, error)
    finally:
        # 超时任务所在线程无法强制杀死，不阻塞调度收尾；其迟到结果会被忽略。
        pool.shutdown(wait=False, cancel_futures=True)

    return RunResult(records)

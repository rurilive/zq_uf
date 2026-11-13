"""支持优先级、租约、状态机和流程隔离的内存任务队列。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from heapq import heappop, heappush
from threading import RLock
from time import monotonic
from typing import Any, Callable, Optional
from uuid import uuid4


DEFAULT_FLOW = "default"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass
class Task:
    task_id: str
    payload: Any
    priority: int = 100
    flow_name: str = DEFAULT_FLOW
    max_attempts: int = 3
    attempts: int = 0
    status: TaskStatus = TaskStatus.PENDING
    enqueued_at: float = 0.0
    sequence: int = 0
    leased_by: Optional[str] = None
    lease_expires_at: Optional[float] = None
    last_error: Optional[str] = None


class TaskQueue:
    def __init__(
        self,
        *,
        default_lease_timeout: float = 30.0,
        default_max_attempts: int = 3,
        clock: Optional[Callable[[], float]] = None,
    ):
        if default_lease_timeout <= 0:
            raise ValueError("default_lease_timeout must be greater than 0")
        if default_max_attempts < 1:
            raise ValueError("default_max_attempts must be greater than 0")

        self.default_lease_timeout = default_lease_timeout
        self.default_max_attempts = default_max_attempts
        self._clock = clock or monotonic
        self._lock = RLock()
        self._sequence = 0
        self._tasks: dict[str, Task] = {}
        self._pending_by_flow: dict[str, list[tuple[int, float, int, str]]] = {}
        self._succeeded: list[Any] = []
        self._failed: list[Any] = []
        self._dead_letter: list[Any] = []

    def push(
        self,
        task: Any,
        *,
        priority: Optional[int] = None,
        flow_name: str = DEFAULT_FLOW,
        max_attempts: Optional[int] = None,
    ) -> Task:
        with self._lock:
            now = self._clock()
            entry = self._build_task(
                task,
                priority=priority,
                flow_name=flow_name,
                max_attempts=max_attempts,
            )
            if entry.task_id in self._tasks:
                raise ValueError(f"duplicate task id: {entry.task_id}")
            self._tasks[entry.task_id] = entry
            self._enqueue_locked(entry, now)
            return entry

    def lease(
        self,
        worker: str,
        flow_name: str = DEFAULT_FLOW,
        *,
        lease_timeout: Optional[float] = None,
    ) -> Any:
        task = self.lease_task(
            worker,
            flow_name,
            lease_timeout=lease_timeout,
        )
        if task is None:
            return None
        return task.payload

    def lease_task(
        self,
        worker: str,
        flow_name: str = DEFAULT_FLOW,
        *,
        lease_timeout: Optional[float] = None,
    ) -> Optional[Task]:
        timeout = lease_timeout or self.default_lease_timeout
        if timeout <= 0:
            raise ValueError("lease_timeout must be greater than 0")

        with self._lock:
            now = self._clock()
            self._reclaim_expired_locked(now)
            heap = self._pending_by_flow.setdefault(flow_name, [])
            self._drop_stale_entries_locked(heap)
            if not heap:
                return None

            *_, task_id = heap[0]
            task = self._tasks[task_id]
            heappop(heap)
            task.attempts += 1
            task.status = TaskStatus.RUNNING
            task.leased_by = worker
            task.lease_expires_at = now + timeout
            return task

    def renew_lease(
        self,
        task: Any,
        worker: Optional[str] = None,
        *,
        lease_timeout: Optional[float] = None,
    ) -> Task:
        timeout = lease_timeout or self.default_lease_timeout
        if timeout <= 0:
            raise ValueError("lease_timeout must be greater than 0")

        with self._lock:
            now = self._clock()
            self._reclaim_expired_locked(now)
            entry = self._require_task_locked(task)
            if entry.status != TaskStatus.RUNNING:
                raise RuntimeError(f"task is not running: {entry.task_id}")
            if worker is not None and entry.leased_by != worker:
                raise RuntimeError(f"task is leased to another worker: {entry.task_id}")
            entry.lease_expires_at = now + timeout
            return entry

    def complete(self, task: Any, *, worker: Optional[str] = None) -> Task:
        with self._lock:
            now = self._clock()
            self._reclaim_expired_locked(now)
            entry = self._require_task_locked(task)
            self._require_running_locked(entry, worker)
            entry.status = TaskStatus.SUCCESS
            entry.leased_by = None
            entry.lease_expires_at = None
            self._succeeded.append(entry.payload)
            return entry

    def fail(
        self,
        task: Any,
        error: Optional[str] = None,
        *,
        retry: bool = True,
        worker: Optional[str] = None,
    ) -> Task:
        with self._lock:
            now = self._clock()
            self._reclaim_expired_locked(now)
            entry = self._require_task_locked(task)
            self._require_running_locked(entry, worker)
            entry.last_error = error
            entry.leased_by = None
            entry.lease_expires_at = None

            if retry and entry.attempts < entry.max_attempts:
                entry.status = TaskStatus.PENDING
                self._enqueue_locked(entry, now)
                return entry

            entry.status = (
                TaskStatus.DEAD_LETTER
                if retry and entry.attempts >= entry.max_attempts
                else TaskStatus.FAILED
            )
            terminal = (
                self._dead_letter
                if entry.status == TaskStatus.DEAD_LETTER
                else self._failed
            )
            terminal.append(entry.payload)
            return entry

    def reclaim_expired(self) -> list[Task]:
        with self._lock:
            return self._reclaim_expired_locked(self._clock())

    def get_task(self, task: Any) -> Task:
        with self._lock:
            return self._require_task_locked(task)

    def get_status(self, task: Any) -> TaskStatus:
        return self.get_task(task).status

    def tasks(self, flow_name: Optional[str] = None) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
            if flow_name is not None:
                tasks = [task for task in tasks if task.flow_name == flow_name]
            return tasks

    def stats(self, flow_name: Optional[str] = None) -> dict[str, int]:
        with self._lock:
            result = {status.value: 0 for status in TaskStatus}
            for task in self.tasks(flow_name):
                result[task.status.value] += 1
            return result

    @property
    def pending(self) -> list[Any]:
        with self._lock:
            entries = [
                task
                for task in self._tasks.values()
                if task.status == TaskStatus.PENDING
            ]
            entries.sort(key=lambda task: (task.priority, task.enqueued_at, task.sequence))
            return [task.payload for task in entries]

    @property
    def running(self) -> list[Any]:
        with self._lock:
            return [
                task.payload
                for task in self._tasks.values()
                if task.status == TaskStatus.RUNNING
            ]

    @property
    def done(self) -> list[Any]:
        with self._lock:
            return list(self._succeeded)

    @property
    def succeeded(self) -> list[Any]:
        return self.done

    @property
    def failed(self) -> list[Any]:
        with self._lock:
            return list(self._failed)

    @property
    def dead_letter(self) -> list[Any]:
        with self._lock:
            return list(self._dead_letter)

    def _build_task(
        self,
        task: Any,
        *,
        priority: Optional[int],
        flow_name: str,
        max_attempts: Optional[int],
    ) -> Task:
        attempts_limit = (
            self.default_max_attempts
            if max_attempts is None
            else max_attempts
        )
        if attempts_limit < 1:
            raise ValueError("max_attempts must be greater than 0")

        if isinstance(task, Task):
            if priority is not None:
                task.priority = priority
            if flow_name != DEFAULT_FLOW:
                task.flow_name = flow_name
            if max_attempts is not None:
                task.max_attempts = max_attempts
            self._validate_task_locked(task)
            return task

        if isinstance(task, dict):
            data = dict(task)
            task_id = data.pop("task_id", None) or data.pop("id", None) or uuid4().hex
            payload = data.pop("payload", data.pop("data", data))
            task_priority = priority if priority is not None else data.pop("priority", 100)
            task_flow = data.pop("flow_name", data.pop("flow", flow_name))
            task_limit = data.pop("max_attempts", attempts_limit)
            if max_attempts is not None:
                task_limit = max_attempts
            entry = Task(
                task_id=str(task_id),
                payload=payload,
                priority=int(task_priority),
                flow_name=str(task_flow),
                max_attempts=int(task_limit),
            )
        else:
            entry = Task(
                task_id=uuid4().hex,
                payload=task,
                priority=100 if priority is None else priority,
                flow_name=flow_name,
                max_attempts=attempts_limit,
            )

        self._validate_task_locked(entry)
        return entry

    @staticmethod
    def _validate_task_locked(task: Task) -> None:
        if not task.task_id:
            raise ValueError("task_id must not be empty")
        if not isinstance(task.priority, int):
            raise ValueError("priority must be an integer")
        if not isinstance(task.flow_name, str) or not task.flow_name:
            raise ValueError("flow_name must be a non-empty string")
        if task.max_attempts < 1:
            raise ValueError("max_attempts must be greater than 0")
        if task.status != TaskStatus.PENDING:
            raise ValueError("new tasks must be pending")

    def _enqueue_locked(self, task: Task, now: float) -> None:
        self._sequence += 1
        task.status = TaskStatus.PENDING
        task.enqueued_at = now
        task.sequence = self._sequence
        task.leased_by = None
        task.lease_expires_at = None
        heap = self._pending_by_flow.setdefault(task.flow_name, [])
        heappush(heap, (task.priority, now, task.sequence, task.task_id))

    def _reclaim_expired_locked(self, now: float) -> list[Task]:
        reclaimed = []
        for task in list(self._tasks.values()):
            if task.status != TaskStatus.RUNNING:
                continue
            if task.lease_expires_at is None or task.lease_expires_at > now:
                continue

            task.leased_by = None
            task.lease_expires_at = None
            task.last_error = "lease timed out"
            if task.attempts >= task.max_attempts:
                task.status = TaskStatus.DEAD_LETTER
                self._dead_letter.append(task.payload)
            else:
                self._enqueue_locked(task, now)
                reclaimed.append(task)
        return reclaimed

    def _drop_stale_entries_locked(self, heap: list[tuple[int, float, int, str]]) -> None:
        while heap:
            priority, enqueued_at, sequence, task_id = heap[0]
            task = self._tasks.get(task_id)
            if (
                task is not None
                and task.status == TaskStatus.PENDING
                and task.priority == priority
                and task.enqueued_at == enqueued_at
                and task.sequence == sequence
            ):
                return
            heappop(heap)

    def _require_task_locked(self, task: Any) -> Task:
        if isinstance(task, Task):
            entry = self._tasks.get(task.task_id)
            if entry is not None:
                return entry
        elif isinstance(task, dict):
            task_id = task.get("task_id") or task.get("id")
            if task_id is not None and task_id in self._tasks:
                return self._tasks[task_id]

        for entry in self._tasks.values():
            if entry.payload == task:
                return entry
        raise ValueError("unknown task")

    @staticmethod
    def _require_running_locked(task: Task, worker: Optional[str]) -> None:
        if task.status != TaskStatus.RUNNING:
            raise RuntimeError(f"task is not running: {task.task_id}")
        if worker is not None and task.leased_by != worker:
            raise RuntimeError(f"task is leased to another worker: {task.task_id}")

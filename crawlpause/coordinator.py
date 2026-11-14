"""按任务的暂停/恢复协调器与采集任务执行器。

阶段（phase）口径：

* ``running``  —— 正常采集；
* ``pausing``  —— 已置只读（不再开新批次/不写新结果），等当前批次收尾；
* ``paused``   —— 批次收尾结束（或超时中断），检查点已落盘；
* ``resuming`` —— 从检查点恢复的瞬间态，随后重新进入 ``running``；
* ``failed``   —— 工作线程处理数据项时异常，已带上下文抛出。

检查点口径：游标 ``safe_cursor`` 只在**批次边界**推进（=已完整收尾的
批次数）。暂停超时只中断当前批次，检查点仍记批次边界；批次内已写入
的结果不回滚，恢复时重放该批次，由 :class:`~crawlpause.store.IdempotentStore`
按幂等键去重，因此实现的是“至少一次 + 幂等入库”。
"""

import json
import os
import tempfile
import threading
import time

from .errors import (
    CheckpointError,
    TaskExecutionError,
    TaskNotPausedError,
    TaskStateError,
)
from .store import IdempotentStore

RUNNING = "running"
PAUSING = "pausing"
PAUSED = "paused"
RESUMING = "resuming"
FAILED = "failed"

# 中断信号发出后，留给工作线程收尾的宽限时间，避免强行 detach 线程。
_INTERRUPT_JOIN_TIMEOUT = 1.0


class _Record:
    """单个任务的全部可变状态，自带锁。"""

    def __init__(self, task):
        self.task = task
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)

        self.phase = RUNNING
        self.pause_in_progress = False
        self.resume_in_progress = False
        self.read_only = threading.Event()   # 置位 = 只读，当前批次也不得再写入
        self.drain_pending = threading.Event()  # 置位 = 收尾中，禁止开新批次
        self.stop_event = None               # 当前批次的中断事件，每批次重建

        self.items = []
        self.batch_size = 1
        self.processor = None
        self.store = None

        self.worker = None
        self.safe_cursor = 0                 # 已完整收尾的批次数（检查点游标）
        self.batch_active = False
        self.current_batch = None            # (start, end)
        self.current_item = None             # (index, key)，供异常与中断定位
        self.processed_count = 0
        self.duplicate_count = 0
        self.error = None
        self.interrupted = False
        self.interrupt_reason = None
        self.pause_result = None
        self.resume_result = None
        self.checkpoint_path = None


class Coordinator:
    """多任务暂停/恢复协调器，每个任务独立阶段与检查点。"""

    def __init__(self, checkpoint_dir=None):
        if checkpoint_dir is None:
            checkpoint_dir = os.path.join(
                tempfile.gettempdir(), "crawlpause-checkpoints"
            )
        self.checkpoint_dir = checkpoint_dir
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        self._registry = {}
        self._registry_lock = threading.Lock()

    # ------------------------------------------------------------------ 注册

    def register(self, crawl_task):
        """把一个 :class:`CrawlTask` 纳入协调器管理。"""
        task = crawl_task.task
        with self._registry_lock:
            if task in self._registry:
                raise TaskStateError(
                    "task %r already registered; duplicate registration rejected"
                    % task
                )
            rec = _Record(task)
            rec.items = crawl_task._items
            rec.batch_size = crawl_task.batch_size
            rec.processor = crawl_task.processor
            rec.store = crawl_task.store
            rec.stop_event = threading.Event()
            crawl_task._record = rec
            self._registry[task] = rec

    def _get(self, task):
        with self._registry_lock:
            return self._registry.get(task)

    def _register_bare(self, task):
        """为只当标志用的 pause(task) 惰性建空记录（兼容无 CrawlTask 的调用）。"""
        with self._registry_lock:
            rec = self._registry.get(task)
            if rec is not None:
                return rec
            rec = _Record(task)
            rec.store = IdempotentStore()
            rec.stop_event = threading.Event()
            self._registry[task] = rec
            return rec

    # ------------------------------------------------------------------ 查询

    def is_paused(self, task):
        """任务处于暂停相关阶段（pausing/paused）即视为已暂停。"""
        rec = self._get(task)
        if rec is None:
            return False
        with rec.lock:
            return rec.phase in (PAUSING, PAUSED)

    def status(self, task):
        """返回任务的完整状态快照；未知任务返回 None。"""
        rec = self._get(task)
        if rec is None:
            return None
        with rec.lock:
            return {
                "task": task,
                "phase": rec.phase,
                "read_only": rec.read_only.is_set(),
                "drain_pending": rec.drain_pending.is_set(),
                "safe_cursor": rec.safe_cursor,
                "processed": rec.processed_count,
                "duplicates": rec.duplicate_count,
                "interrupted": rec.interrupted,
                "interrupt_reason": rec.interrupt_reason,
                "error": None if rec.error is None else str(rec.error),
                "checkpoint_path": rec.checkpoint_path,
            }

    # ------------------------------------------------------------------ 暂停

    def pause(self, task, drain_timeout=5.0):
        """暂停任务。

        步骤：置收尾态（不开新批次）-> 等当前批次收尾（最长
        ``drain_timeout`` 秒）-> 超时升级只读、中断当前批次并记录原因 ->
        落检查点。

        幂等：任务已在 pausing/paused 时直接返回上次结果，不改变阶段、
        不重写检查点。未注册的任务名会惰性建空记录（兼容纯标志用法）。
        """
        rec = self._get(task)
        if rec is None:
            rec = self._register_bare(task)

        with rec.lock:
            if rec.phase == PAUSED:
                # 幂等：暂停已完成，重复调用返回缓存结果，不重写检查点。
                return rec.pause_result
            if rec.phase == PAUSING:
                # 另一个暂停正在收尾：等它完成，再返回同一份结果。
                while rec.phase == PAUSING:
                    rec.cond.wait(timeout=1.0)
                if rec.phase != PAUSED:
                    raise TaskStateError(
                        "task %r no longer paused (phase=%r); pause retry rejected"
                        % (task, rec.phase)
                    )
                return rec.pause_result
            if rec.phase == FAILED:
                raise TaskStateError(
                    "task %r has failed: %s" % (task, rec.error)
                )
            if rec.pause_in_progress:
                raise TaskStateError(
                    "task %r pause is already in progress; concurrent call rejected"
                    % task
                )
            rec.pause_in_progress = True
            rec.phase = PAUSING
            # 第一阶段：只禁止开新批次，当前批次可以继续写直到自然收尾。
            rec.drain_pending.set()
            deadline = time.monotonic() + max(0.0, drain_timeout)

        try:
            return self._pause_locked(rec, task, deadline, drain_timeout)
        finally:
            with rec.lock:
                rec.pause_in_progress = False

    def _pause_locked(self, rec, task, deadline, drain_timeout):
        # 等当前批次收尾（批次完成会 broadcast）。
        with rec.lock:
            while rec.batch_active:
                if rec.error is not None:
                    rec.phase = FAILED
                    raise TaskExecutionError(
                        "task %r failed while waiting for batch drain: %s"
                        % (task, rec.error)
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                rec.cond.wait(timeout=remaining)

        with rec.lock:
            if rec.error is not None:
                rec.phase = FAILED
                raise TaskExecutionError(
                    "task %r failed while waiting for batch drain: %s"
                    % (task, rec.error)
                )
            timed_out = rec.batch_active
            if timed_out:
                # 超时：升级为只读并中断当前批次，记录原因。
                rec.read_only.set()
                rec.interrupted = True
                rec.interrupt_reason = (
                    "drain timeout after %.3fs while batch %s was still running"
                    % (drain_timeout, rec.current_batch)
                )
                if rec.stop_event is not None:
                    rec.stop_event.set()

        if timed_out:
            # 给工作线程一点时间响应中断、退出到批次边界。
            if rec.worker is not None and rec.worker.is_alive():
                rec.worker.join(_INTERRUPT_JOIN_TIMEOUT)

        with rec.lock:
            if rec.error is not None:
                rec.phase = FAILED
                raise TaskExecutionError(
                    "task %r failed during interrupt: %s" % (task, rec.error)
                )
            checkpoint = self._write_checkpoint(rec, timed_out)
            rec.phase = PAUSED
            result = {
                "task": task,
                "paused": True,
                "phase": PAUSED,
                "interrupted": rec.interrupted,
                "interrupt_reason": rec.interrupt_reason,
                "drain_timed_out": timed_out,
                "checkpoint": checkpoint,
            }
            rec.pause_result = result
            rec.cond.notify_all()
            return result

    # ------------------------------------------------------------------ 恢复

    def resume(self, task):
        """从检查点恢复任务。

        幂等：任务不在 paused 时不改变阶段——已在运行的任务返回上次恢复
        结果；从未暂停/未知任务抛 :class:`TaskNotPausedError`。
        """
        rec = self._get(task)
        if rec is None:
            raise TaskNotPausedError(
                "cannot resume unknown task %r; nothing to resume" % task
            )

        with rec.lock:
            if rec.phase == PAUSED:
                if rec.resume_in_progress:
                    raise TaskStateError(
                        "task %r resume is already in progress; concurrent call rejected"
                        % task
                    )
                rec.resume_in_progress = True
            elif rec.phase in (RUNNING, RESUMING):
                if rec.resume_result is not None:
                    return rec.resume_result
                raise TaskStateError(
                    "task %r is %s, not paused; resume rejected"
                    % (task, rec.phase)
                )
            elif rec.phase == PAUSING:
                raise TaskStateError(
                    "task %r is still pausing; wait for pause to finish" % task
                )
            elif rec.phase == FAILED:
                raise TaskStateError(
                    "task %r has failed: %s" % (task, rec.error)
                )
            else:  # pragma: no cover - 防御性分支
                raise TaskStateError(
                    "task %r in unexpected phase %r" % (task, rec.phase)
                )

        try:
            with rec.lock:
                checkpoint = self._read_checkpoint(rec)
                rec.phase = RESUMING
                resume_cursor = checkpoint["safe_cursor"]
                if resume_cursor != rec.safe_cursor:
                    raise CheckpointError(
                        "task %r checkpoint cursor %d disagrees with in-memory "
                        "cursor %d; refusing to resume"
                        % (task, resume_cursor, rec.safe_cursor)
                    )

                # 清暂停态：新代际，旧中断事件全部失效。
                rec.stop_event = threading.Event()
                rec.read_only.clear()
                rec.drain_pending.clear()
                rec.interrupted = False
                rec.interrupt_reason = None
                rec.pause_result = None
                resumed_from = resume_cursor
                skipped_before = rec.duplicate_count
                rec.phase = RUNNING

            self._spawn_worker(rec)

            with rec.lock:
                result = {
                    "task": task,
                    "resumed": True,
                    "phase": RUNNING,
                    "resumed_from_cursor": resumed_from,
                    "checkpoint": {
                        "path": rec.checkpoint_path,
                        "safe_cursor": resumed_from,
                    },
                    "duplicates_so_far": skipped_before,
                }
                rec.resume_result = result
                rec.cond.notify_all()
                return result
        except BaseException:
            # 失败回滚到 paused，保证任务仍可再次尝试恢复。
            with rec.lock:
                rec.phase = PAUSED
                rec.drain_pending.set()
            raise
        finally:
            with rec.lock:
                rec.resume_in_progress = False

    # ------------------------------------------------------------------ 检查点

    def _checkpoint_path(self, rec):
        return os.path.join(
            self.checkpoint_dir, "checkpoint-%s.json" % rec.task
        )

    def _write_checkpoint(self, rec, timed_out):
        rec.checkpoint_path = self._checkpoint_path(rec)
        payload = {
            "version": 1,
            "task": rec.task,
            "safe_cursor": rec.safe_cursor,
            "total_batches": (
                len(rec.items) + rec.batch_size - 1
            ) // rec.batch_size,
            "total_items": len(rec.items),
            "processed_count": rec.processed_count,
            "duplicate_count": rec.duplicate_count,
            "interrupted": rec.interrupted,
            "interrupt_reason": rec.interrupt_reason,
            "saved_at": time.time(),
        }
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".checkpoint-", suffix=".tmp", dir=self.checkpoint_dir
            )
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, rec.checkpoint_path)  # 原子替换
        except OSError as exc:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise CheckpointError(
                "failed to write checkpoint for task %r at %s: %s"
                % (rec.task, rec.checkpoint_path, exc)
            ) from exc
        return {
            "path": rec.checkpoint_path,
            "safe_cursor": rec.safe_cursor,
            "interrupted": rec.interrupted,
        }

    def _read_checkpoint(self, rec):
        path = rec.checkpoint_path or self._checkpoint_path(rec)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise CheckpointError(
                "failed to read checkpoint for task %r at %s: %s"
                % (rec.task, path, exc)
            ) from exc
        if data.get("task") != rec.task:
            raise CheckpointError(
                "checkpoint at %s belongs to task %r, not %r"
                % (path, data.get("task"), rec.task)
            )
        return data

    # ------------------------------------------------------------------ 工作线程

    def _spawn_worker(self, rec):
        t = threading.Thread(
            target=_worker_loop,
            name="crawlpause-worker-%s" % rec.task,
            args=(self, rec),
            daemon=True,
        )
        rec.worker = t
        t.start()

    # 由 CrawlTask.start 复用，保持“启动工作线程”只有这一处。
    _start_worker = _spawn_worker


def _worker_loop(coordinator, rec):
    """采集主循环：按批次处理，在批次边界与只读/中断信号协作。"""
    try:
        total = len(rec.items)
        bs = rec.batch_size
        batch_index = rec.safe_cursor  # 从检查点批次边界继续
        while batch_index * bs < total:
            start = batch_index * bs
            end = min(start + bs, total)

            with rec.lock:
                # 收尾中（禁止新批次）或已只读：不开新批次，退到边界外等待。
                if rec.drain_pending.is_set() or rec.read_only.is_set():
                    rec.cond.notify_all()
                    return
                my_event = rec.stop_event
                rec.batch_active = True
                rec.current_batch = (start, end)

            for index in range(start, end):
                with rec.lock:
                    if my_event is not None and my_event.is_set():
                        break
                    if rec.read_only.is_set():
                        break
                    item = rec.items[index]
                    rec.current_item = (index, item["key"])
                try:
                    value = rec.processor(item)
                except Exception as exc:
                    with rec.lock:
                        rec.error = TaskExecutionError(
                            "task %r processor failed on item index=%d key=%r: %s"
                            % (rec.task, index, item["key"], exc)
                        )
                        rec.batch_active = False
                        rec.phase = FAILED
                        rec.cond.notify_all()
                    return

                with rec.lock:
                    if rec.read_only.is_set():
                        # 结果已算出但任务只读：不写入，留给恢复重放。
                        break
                    inserted = rec.store.put(item["key"], value)
                    if inserted:
                        rec.processed_count += 1
                    else:
                        rec.duplicate_count += 1

            with rec.lock:
                finished_batch = not (
                    rec.read_only.is_set()
                    or (my_event is not None and my_event.is_set())
                )
                rec.batch_active = False
                rec.current_batch = None
                rec.current_item = None
                if finished_batch:
                    # 只有完整收尾的批次才推进检查点游标。
                    rec.safe_cursor = batch_index + 1
                    rec.resume_result = None
                    batch_index += 1
                rec.cond.notify_all()
                if not finished_batch:
                    return
    except Exception as exc:  # pragma: no cover - 防御性兜底
        with rec.lock:
            rec.error = TaskExecutionError(
                "task %r worker loop crashed: %s" % (rec.task, exc)
            )
            rec.phase = FAILED
            rec.cond.notify_all()


class CrawlTask:
    """一个模拟采集任务：把 items 按 batch_size 分批处理并写入幂等存储。

    :param task: 任务 id（任意可哈希值，通常是字符串）。
    :param items: 数据项列表，每项是 ``{"key": 幂等键, "data": ...}``；
                  也接受 ``(key, data)`` 二元组。
    :param processor: 处理函数，入参为数据项 dict，返回要入库的值；
                      默认返回 ``data`` 原样。
    :param batch_size: 每批条数，必须 >= 1。
    :param store: 自定义 :class:`IdempotentStore`，默认新建。
    :param coordinator: 所属协调器，默认用模块级单例。
    """

    def __init__(
        self,
        task,
        items,
        processor=None,
        batch_size=10,
        store=None,
        coordinator=None,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1, got %r" % batch_size)
        self.task = task
        self.batch_size = batch_size
        self.processor = processor or (lambda item: item["data"])
        self.store = store or IdempotentStore()
        self._items = [self._normalize(item) for item in items]
        self._coordinator = coordinator or get_coordinator()
        self._record = None
        self._coordinator.register(self)

    @staticmethod
    def _normalize(item):
        if isinstance(item, dict) and "key" in item:
            return {"key": item["key"], "data": item.get("data")}
        if isinstance(item, tuple) and len(item) == 2:
            return {"key": item[0], "data": item[1]}
        raise ValueError(
            "each item must be {'key': ..., 'data': ...} or a (key, data) tuple"
        )

    def start(self):
        """启动后台工作线程；重复调用幂等，已在跑则直接返回。"""
        rec = self._record
        with rec.lock:
            if rec.worker is not None and rec.worker.is_alive():
                return
        self._coordinator._start_worker(rec)

    def wait_done(self, timeout=None):
        """阻塞等任务全部批次处理完（或暂停/失败退出）。"""
        rec = self._record
        worker = rec.worker
        if worker is not None:
            worker.join(timeout)

    def wait_batch_started(self, timeout=5.0):
        """等到至少一个批次开始执行，供测试稳定复现“批次在跑”。"""
        rec = self._record
        deadline = time.monotonic() + timeout
        with rec.lock:
            while not rec.batch_active and rec.phase == RUNNING:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "task %r batch did not start within %ss"
                        % (self.task, timeout)
                    )
                rec.cond.wait(timeout=remaining)

    def stats(self):
        rec = self._record
        with rec.lock:
            return {
                "task": self.task,
                "phase": rec.phase,
                "safe_cursor": rec.safe_cursor,
                "processed": rec.processed_count,
                "duplicates": rec.duplicate_count,
                "stored": rec.store.size(),
            }


# ---------------------------------------------------------------------- 模块级单例

_default_coordinator = None
_default_lock = threading.Lock()


def get_coordinator():
    """模块级默认协调器（``crawlpause.pause`` 等函数背后的单例）。"""
    global _default_coordinator
    with _default_lock:
        if _default_coordinator is None:
            _default_coordinator = Coordinator()
        return _default_coordinator

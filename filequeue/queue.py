"""带可见性超时的持久化文件队列（纯标准库）。

消息以 ``序号.msg`` 文件落盘，每条记录带序号、校验和、状态与投递信息。
同一队列目录上的所有操作经 ``flock`` 串行化，因此多线程、多进程下同一
消息同一时刻只会被一个消费者持有；消费者崩溃后租约到期自动重投，超过
投递上限进入死信状态。确认只对“不可见且仍在租约中”的消息生效，重复
确认与确认不可见/不存在的消息都返回 ``False``，不抛异常（幂等）。
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
import threading

STATE_VISIBLE = "visible"
STATE_INVISIBLE = "invisible"
STATE_DEAD = "dead"

MSG_SUFFIX = ".msg"
SEQ_FILE = "seq"
LOCK_FILE = ".lock"

# 默认 visibility_timeout=0：保持历史 get() 语义（取走后立即可再次被取到）。
# 生产场景应通过 configure 设置正数超时；demo 与 tests 均显式配置。
DEFAULT_VISIBILITY_TIMEOUT = 0.0
DEFAULT_MAX_DELIVERIES = 5


def _checksum(seq: int, payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    payload_digest = hashlib.sha256(blob).hexdigest()
    return hashlib.sha256(
        b"filequeue\x00" + str(seq).encode("ascii") + b"\x00" +
        payload_digest.encode("ascii")).hexdigest()


class FileQueue:
    """单一队列目录上的持久化文件队列。"""

    def __init__(self, data_dir: str, visibility_timeout: float = DEFAULT_VISIBILITY_TIMEOUT,
                 max_deliveries: int = DEFAULT_MAX_DELIVERIES):
        self.data_dir = data_dir
        self.visibility_timeout = visibility_timeout
        self.max_deliveries = max_deliveries
        self._clock_offset = 0.0
        self._lock_handle = None
        # flock 只在进程间互斥；同进程多线程共享 fd 上的锁，需要进程内锁兜底。
        self._thread_lock = threading.RLock()
        os.makedirs(self.data_dir, exist_ok=True)

    # ---- 时钟与锁 -------------------------------------------------------

    def _now(self) -> float:
        return time.time() + self._clock_offset

    def advance(self, seconds: float) -> None:
        """测试用时间缝：把队列时钟向后拨 ``seconds`` 秒。"""
        self._clock_offset += seconds

    @contextlib.contextmanager
    def _locked(self):
        if self._lock_handle is None:
            self._lock_handle = open(os.path.join(self.data_dir, LOCK_FILE), "a+")
        with self._thread_lock:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)

    # ---- 落盘 -----------------------------------------------------------

    def _path(self, seq: int) -> str:
        return os.path.join(self.data_dir, f"{seq:020d}{MSG_SUFFIX}")

    def _fsync_dir(self) -> None:
        fd = os.open(self.data_dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_write(self, path: str, record: dict) -> None:
        directory = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            self._fsync_dir()
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    def _write_record(self, record: dict) -> None:
        self._atomic_write(self._path(record["seq"]), record)

    def _read_records(self) -> list:
        records = []
        for name in sorted(os.listdir(self.data_dir)):
            if not name.endswith(MSG_SUFFIX):
                continue
            path = os.path.join(self.data_dir, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                continue
            records.append(record)
        return records

    def _next_seq(self) -> int:
        path = os.path.join(self.data_dir, SEQ_FILE)
        seq = 0
        try:
            with open(path, encoding="utf-8") as handle:
                seq = int(handle.read().strip() or "0")
        except (FileNotFoundError, ValueError):
            seq = 0
        seq += 1
        fd, tmp = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(seq))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, os.path.join(self.data_dir, SEQ_FILE))
            self._fsync_dir()
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        return seq

    # ---- 状态机 ---------------------------------------------------------

    def _sweep(self, now: float) -> None:
        """把到期的不可见消息重投或转入死信。"""
        for record in self._read_records():
            if record.get("state") != STATE_INVISIBLE:
                continue
            if record.get("visible_at", 0.0) > now:
                continue
            if record.get("deliveries", 0) >= self.max_deliveries:
                record["state"] = STATE_DEAD
                record["dead_reason"] = "max_deliveries_exceeded"
                record.pop("visible_at", None)
                record.pop("consumer", None)
            else:
                record["state"] = STATE_VISIBLE
                record.pop("visible_at", None)
                record.pop("consumer", None)
            self._write_record(record)

    @staticmethod
    def _envelope(record: dict) -> dict:
        envelope = dict(record["payload"])
        envelope["msg_id"] = record["seq"]
        envelope["deliveries"] = record.get("deliveries", 0)
        return envelope

    # ---- 公开接口 -------------------------------------------------------

    def put(self, msg: dict) -> int:
        record_payload = dict(msg)
        with self._locked():
            seq = self._next_seq()
            record = {
                "seq": seq,
                "checksum": _checksum(seq, record_payload),
                "state": STATE_VISIBLE,
                "deliveries": 0,
                "payload": record_payload,
            }
            self._write_record(record)
            return seq

    def get(self, consumer: str):
        with self._locked():
            now = self._now()
            self._sweep(now)
            for record in self._read_records():
                if record.get("state") != STATE_VISIBLE:
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or \
                        record.get("checksum") != _checksum(record["seq"], payload):
                    record["state"] = STATE_DEAD
                    record["dead_reason"] = "checksum_mismatch"
                    record.pop("visible_at", None)
                    record.pop("consumer", None)
                    self._write_record(record)
                    continue
                record["state"] = STATE_INVISIBLE
                record["consumer"] = consumer
                record["deliveries"] = record.get("deliveries", 0) + 1
                record["visible_at"] = now + self.visibility_timeout
                self._write_record(record)
                return self._envelope(record)
            return None

    def ack(self, msg_id: int) -> bool:
        """确认仍在租约中的消息并删除；其它情况为幂等空操作。"""
        path = self._path(int(msg_id))
        with self._locked():
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except (FileNotFoundError, ValueError, OSError):
                return False
            if record.get("state") != STATE_INVISIBLE:
                return False
            os.unlink(path)
            self._fsync_dir()
            return True

    def stats(self) -> dict:
        """返回 visible/invisible/dead 数量与累计重投次数。"""
        with self._locked():
            self._sweep(self._now())
            counts = {STATE_VISIBLE: 0, STATE_INVISIBLE: 0, STATE_DEAD: 0}
            redeliveries = 0
            for record in self._read_records():
                state = record.get("state")
                if state in counts:
                    counts[state] += 1
                redeliveries += max(0, record.get("deliveries", 0) - 1)
            return {
                "visible": counts[STATE_VISIBLE],
                "invisible": counts[STATE_INVISIBLE],
                "dead": counts[STATE_DEAD],
                "redeliveries": redeliveries,
            }

    def close(self) -> None:
        if self._lock_handle is not None:
            self._lock_handle.close()
            self._lock_handle = None

    def destroy(self) -> None:
        """删除队列目录中的全部数据（测试/演示用）。"""
        self.close()
        with contextlib.suppress(FileNotFoundError):
            for name in os.listdir(self.data_dir):
                os.unlink(os.path.join(self.data_dir, name))
            os.rmdir(self.data_dir)


# ---- 模块级单例与历史接口 -----------------------------------------------

_queue = None


def _get_queue() -> FileQueue:
    global _queue
    if _queue is None:
        _queue = FileQueue(tempfile.mkdtemp(prefix="filequeue-"))
    return _queue


def configure(data_dir=None, visibility_timeout=DEFAULT_VISIBILITY_TIMEOUT,
              max_deliveries=DEFAULT_MAX_DELIVERIES) -> FileQueue:
    """替换模块级队列实例；``data_dir`` 指向已有目录时即恢复历史消息。"""
    global _queue
    if _queue is not None:
        _queue.close()
    if data_dir is None:
        data_dir = tempfile.mkdtemp(prefix="filequeue-")
    _queue = FileQueue(data_dir, visibility_timeout, max_deliveries)
    return _queue


def reset() -> None:
    """清空并重建模块级队列，恢复默认配置（兼容历史无参签名）。"""
    global _queue
    if _queue is not None:
        _queue.destroy()
    _queue = None
    _get_queue()


def advance(seconds: float) -> None:
    """测试用：推进模块级队列时钟。"""
    _get_queue().advance(seconds)


def stats() -> dict:
    return _get_queue().stats()


def put(msg: dict) -> int:
    return _get_queue().put(msg)


def get(consumer: str):
    return _get_queue().get(consumer)


def ack(msg_id: int) -> bool:
    return _get_queue().ack(msg_id)

"""采集任务一致性检查点。

口径：
- save 先把检查点（入库位点 + 已入库页 + 校验和）写入临时文件，fsync 后
  原子改名落盘，落盘成功才确认 durable=True；
- 每个采集任务独占一个目录，互不覆盖；
- 每个任务保留最近 keep_versions 个版本；
- resume 从新到旧逐版本校验，损坏（解析失败或校验和不符）即跳过，回退到
  上一有效点；resume 只读、可重复、可中断，重复调用返回同一结果。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import threading
import uuid
from urllib.parse import quote

DEFAULT_KEEP_VERSIONS = 3

_FILE_PREFIX = "checkpoint-"
_FILE_SUFFIX = ".json"
_TMP_SUFFIX = ".tmp"
_FORMAT = 1


def _canonical(payload):
    """校验和的规范化字节序列，保证跨进程/跨机器一致。"""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _checksum_of(payload):
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _derive_position(data):
    """从调用方状态推导入库位点与已入库页清单。

    优先级：显式 position > stored_pages 的最大页号 > page > 0。
    """
    raw_pages = data.get("stored_pages")
    stored_pages = sorted(raw_pages) if raw_pages else []

    if "position" in data:
        position = int(data["position"])
    elif stored_pages:
        position = int(max(stored_pages))
    elif "page" in data:
        position = int(data["page"])
    else:
        position = 0

    if position < 0:
        raise ValueError("入库位点不能为负")
    return stored_pages, position


def _fsync_dir(path):
    """目录项改名落盘，失败不影响正确性，尽力而为。"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class CheckpointStore:
    """按任务隔离的检查点存储；checkpoint_dir=None 时为纯内存模式。"""

    def __init__(self, checkpoint_dir=None, keep_versions=DEFAULT_KEEP_VERSIONS):
        self.dir = checkpoint_dir
        self.keep_versions = max(1, int(keep_versions))
        self._mem = {}
        self._locks = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _task_lock(self, task):
        with self._locks_guard:
            lock = self._locks.get(task)
            if lock is None:
                lock = threading.Lock()
                self._locks[task] = lock
            return lock

    def _task_dir(self, task):
        # quote 保证任意任务名（含 /、空格、中文）映射到唯一且安全的目录名
        return os.path.join(self.dir, quote(str(task), safe=""))

    @staticmethod
    def _is_version_file(name):
        return (
            name.startswith(_FILE_PREFIX)
            and name.endswith(_FILE_SUFFIX)
            and not name.endswith(_TMP_SUFFIX)
        )

    def _version_files(self, task_dir):
        try:
            names = os.listdir(task_dir)
        except FileNotFoundError:
            return []
        names = [n for n in names if self._is_version_file(n)]
        # 文件名含零填充版本号，字典序即时间序
        return sorted(os.path.join(task_dir, n) for n in names)

    def _next_version(self, task_dir):
        versions = []
        for path in self._version_files(task_dir):
            head = os.path.basename(path)[len(_FILE_PREFIX):-len(_FILE_SUFFIX)]
            num = head.split("-", 1)[0]
            try:
                versions.append(int(num))
            except ValueError:
                continue
        return (max(versions) + 1) if versions else 1

    @staticmethod
    def _cleanup_tmps(task_dir):
        """清理上次写一半崩溃遗留的临时文件。"""
        try:
            names = os.listdir(task_dir)
        except FileNotFoundError:
            return
        for name in names:
            if name.startswith(_FILE_PREFIX) and name.endswith(_TMP_SUFFIX):
                try:
                    os.unlink(os.path.join(task_dir, name))
                except FileNotFoundError:
                    pass

    def _prune(self, task_dir):
        paths = self._version_files(task_dir)
        for old in paths[: -self.keep_versions]:
            try:
                os.unlink(old)
            except FileNotFoundError:
                pass

    @staticmethod
    def _load_record(path):
        """读取并校验一个检查点文件；任何损坏都返回 None。"""
        try:
            with open(path, "rb") as f:
                raw = f.read()
            record = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(record, dict) or not isinstance(
            record.get("checksum"), str
        ):
            return None

        payload = {k: v for k, v in record.items() if k != "checksum"}
        if not hmac.compare_digest(record["checksum"], _checksum_of(payload)):
            return None

        for key in ("format", "task", "version", "position", "stored_pages", "state"):
            if key not in payload:
                return None
        if not isinstance(payload["position"], int) or payload["position"] < 0:
            return None
        if not isinstance(payload["stored_pages"], list):
            return None
        if not isinstance(payload["state"], dict):
            return None
        return record

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def save(self, task, state):
        """记录检查点。内存模式保持旧行为；磁盘模式先落盘再确认。"""
        data = dict(state)

        if self.dir is None:
            self._mem[task] = data
            return {
                "task": task,
                "saved": True,
                "durable": False,
                "checksum": None,
            }

        stored_pages, position = _derive_position(data)

        with self._task_lock(task):
            task_dir = self._task_dir(task)
            os.makedirs(task_dir, exist_ok=True)
            self._cleanup_tmps(task_dir)

            version = self._next_version(task_dir)
            payload = {
                "format": _FORMAT,
                "task": str(task),
                "version": version,
                "position": position,
                "stored_pages": stored_pages,
                "state": data,
            }
            checksum = _checksum_of(payload)
            record = dict(payload)
            record["checksum"] = checksum
            body = json.dumps(
                record, sort_keys=True, ensure_ascii=False, indent=2
            ).encode("utf-8")

            # 临时文件 + fsync + 原子改名：改名前崩溃只留下 .tmp，不污染有效版本
            fd, tmp_path = tempfile.mkstemp(
                prefix=_FILE_PREFIX, suffix=_TMP_SUFFIX, dir=task_dir
            )
            published = None
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                token = uuid.uuid4().hex[:8]
                final_name = (
                    f"{_FILE_PREFIX}{version:010d}-{token}{_FILE_SUFFIX}"
                )
                final_path = os.path.join(task_dir, final_name)
                os.replace(tmp_path, final_path)
                published = final_path
                _fsync_dir(task_dir)
            finally:
                if published is None and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            self._prune(task_dir)

            return {
                "task": task,
                "saved": True,
                "durable": True,
                "checksum": checksum,
                "position": position,
                "version": version,
                "path": published,
            }

    def resume(self, task):
        """恢复位点。只读操作：可重复调用、可中断，结果幂等。"""
        if self.dir is None:
            # 内存模式维持旧口径：从零开始
            return {
                "task": task,
                "start": 0,
                "skipped": [],
                "rolled_back": False,
            }

        result = {
            "task": task,
            "start": 0,
            "position": 0,
            "skipped": [],
            "rolled_back": False,
            "recovered": False,
            "checksum": None,
            "version": None,
        }

        paths = self._version_files(self._task_dir(task))
        if not paths:
            return result

        newest_path = paths[-1]
        record = None
        used_path = None
        for path in reversed(paths):  # 从最新版本向旧版本找第一个有效点
            candidate = self._load_record(path)
            if candidate is not None:
                record = candidate
                used_path = path
                break

        if record is None:
            # 所有版本都损坏：没有可信任的位点，只能从零重跑
            result["rolled_back"] = True
            return result

        position = record["position"]
        stored_pages = record["stored_pages"]
        if stored_pages:
            skipped = sorted(stored_pages)
        elif position:
            # 只有位点时按连续页 1..position 跳过
            skipped = list(range(1, position + 1))
        else:
            skipped = []

        result.update(
            start=position + 1 if position else 0,
            position=position,
            skipped=skipped,
            rolled_back=os.path.abspath(used_path)
            != os.path.abspath(newest_path),
            recovered=True,
            checksum=record["checksum"],
            version=record["version"],
        )
        return result


# ---------------------------------------------------------------------- #
# 模块级接口：默认内存模式（与旧行为一致），configure 启用磁盘一致性模式
# ---------------------------------------------------------------------- #
_env_dir = os.environ.get("TASKRESUME_CHECKPOINT_DIR")
_default = CheckpointStore(_env_dir) if _env_dir else CheckpointStore(None)


def save(task, state):
    """保存检查点，接口签名保持 save(task, state)。"""
    return _default.save(task, state)


def resume(task):
    """恢复检查点，接口签名保持 resume(task)。"""
    return _default.resume(task)


def configure(checkpoint_dir=None, keep_versions=DEFAULT_KEEP_VERSIONS):
    """切换模块级默认存储。

    checkpoint_dir 为目录路径时启用落盘一致性检查点；为 None 时回到纯内存
    模式（默认，旧行为）。返回底层存储实例，便于测试断言。
    """
    global _default
    _default = CheckpointStore(checkpoint_dir, keep_versions=keep_versions)
    return _default


def reset():
    """恢复模块级默认存储为纯内存模式。"""
    configure(None)

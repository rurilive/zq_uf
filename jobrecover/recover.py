"""采集任务阶段级检查点与恢复（纯标准库）。

能力口径：
- 检查点按采集任务隔离，落盘到 ``<base_dir>/<job>/``，任务之间不共享任何状态；
- 每个检查点记录阶段、抓取位点、阶段状态版本号与 sha256 校验和；
- 临时文件 + 原子替换写入，写一半损坏不影响旧检查点，恢复时自动回退上一有效点；
- 恢复时校验阶段依赖（已完成阶段必须是流水线的连续前缀）；
- ``save`` 失败不推进阶段，``resume`` 只读、重复调用返回同一结果；
- 阶段耗时通过 logging 写日志。

模块级 :func:`save` / :func:`resume` 是兼容旧接口的内存版（已弃用），新代码请使用
:class:`JobRecovery`，二者方法名与签名保持一致。
"""

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
from urllib.parse import quote

try:  # 平台为 Linux；保留无 fcntl 环境的降级路径
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

logger = logging.getLogger("jobrecover")

#: 默认阶段流水线，后者依赖前者全部完成
DEFAULT_STAGES = ("start", "fetch", "parse", "store", "finish")

FORMAT_VERSION = 1
_RETENTION = 5  # 每个任务保留的检查点世代数
_CP_PREFIX = "cp-"
_CP_SUFFIX = ".json"
_POINTER = "current.json"
_LOCK = "lock"


class CheckpointError(Exception):
    """检查点读写、序列化或校验失败。"""


class StageDependencyError(CheckpointError):
    """阶段依赖不满足：跳阶段、未知阶段或逆序保存。"""


# --------------------------------------------------------------------------
# 兼容旧接口的内存版函数（行为与历史版本完全一致，旧测试锁定，勿改语义）
# --------------------------------------------------------------------------

_points = {}


def save(job, stage, state):
    """[已弃用] 内存版保存，只存阶段名和状态，没有位点和校验和。"""
    _points[job] = {"stage": stage, "state": dict(state)}
    return {"job": job, "stage": stage, "saved": True, "checksum": None}


def resume(job):
    """[已弃用] 内存版恢复，一律从起始阶段重来。"""
    return {"job": job, "stage": "start", "skipped": [], "restored": False}


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def _safe_job_name(job):
    """把任务标识编码成安全的目录名，杜绝路径穿越。"""
    if not isinstance(job, str) or not job:
        raise CheckpointError("job 必须是非空字符串")
    return quote(job, safe="")


def _canonical(payload):
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _checksum(payload):
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def _atomic_write(path, data):
    """同目录临时文件 + fsync + 原子替换；失败不留半成品、不覆盖旧文件。"""
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(
        prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _validate(payload, job, stages):
    """校验一个已解析的检查点，返回 None 表示有效，否则返回损坏原因。"""
    if not isinstance(payload, dict):
        return "检查点不是 JSON 对象"
    if payload.get("format_version") != FORMAT_VERSION:
        return "格式版本号不匹配"
    if payload.get("job") != job:
        return "任务标识不匹配"
    checksum = payload.get("checksum")
    if not isinstance(checksum, str) or not checksum.startswith("sha256:"):
        return "缺少校验和"
    body = {key: value for key, value in payload.items() if key != "checksum"}
    if _checksum(body) != checksum:
        return "校验和不一致"
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return "状态版本号非法"
    stage = payload.get("stage")
    if stage not in stages:
        return "未知阶段 %r" % (stage,)
    completed = payload.get("completed_stages")
    if not isinstance(completed, list) or completed != list(stages[: len(completed)]):
        return "已完成阶段不是流水线的连续前缀"
    stage_states = payload.get("stages")
    if not isinstance(stage_states, dict):
        return "缺少各阶段状态记录"
    if not isinstance(payload.get("stage_complete"), bool):
        return "缺少 stage_complete 标志"
    completed_set = set(completed)
    if payload["stage_complete"]:
        # 阶段完成点：stage 必须是 completed 末尾，且其状态已落盘
        if not completed or completed[-1] != stage:
            return "完成点阶段与已完成阶段末尾不一致"
        if completed_set != set(stage_states):
            return "各阶段状态记录不完整"
    else:
        # 位点推进点：stage 是第一个未完成阶段；其状态可存在于 stage_states，
        # 但已完成阶段的状态一个都不能缺
        if len(completed) >= len(stages) or stages[len(completed)] != stage:
            return "位点点阶段不是首个未完成阶段"
        if not completed_set.issubset(set(stage_states)):
            return "各阶段状态记录不完整"
        if set(stage_states) - completed_set not in (set(), {stage}):
            return "位点点包含未知阶段状态"
    for entry in stage_states.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), int):
            return "阶段状态缺少版本号"
    return None


class _StageContext:
    """``stage()`` 上下文对象，工作代码往里写状态，正常退出时自动落盘。"""

    def __init__(self, job, stage_name):
        self.job = job
        self.stage = stage_name
        self.state = {}

    @property
    def position(self):
        return self.state.get("position")

    @position.setter
    def position(self, value):
        self.state["position"] = value


class JobRecovery:
    """阶段级检查点存储，方法签名保持 ``save(job, stage, state)`` / ``resume(job)``。"""

    def __init__(self, base_dir, stages=DEFAULT_STAGES):
        if not stages or not all(isinstance(s, str) and s for s in stages):
            raise CheckpointError("stages 必须是非空字符串序列")
        self._base = os.path.abspath(base_dir)
        self._stages = tuple(stages)

    # -- 路径与锁 --------------------------------------------------------

    def _job_dir(self, job):
        return os.path.join(self._base, _safe_job_name(job))

    @contextlib.contextmanager
    def _locked(self, job):
        job_dir = self._job_dir(job)
        os.makedirs(job_dir, exist_ok=True)
        lock_path = os.path.join(job_dir, _LOCK)
        lock_file = open(lock_path, "a+b")
        try:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    # -- 读取链路 --------------------------------------------------------

    def _load_file(self, path, job):
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        reason = _validate(payload, job, self._stages)
        if reason is not None:
            raise CheckpointError("检查点 %s 损坏：%s" % (path, reason))
        return payload

    def _read_pointer(self, job_dir):
        """返回指针声明的当前世代文件名；指针本身损坏时返回 None。"""
        pointer_path = os.path.join(job_dir, _POINTER)
        if not os.path.exists(pointer_path):
            return None
        try:
            with open(pointer_path, "rb") as handle:
                pointer = json.loads(handle.read().decode("utf-8"))
            candidate = pointer["file"]
            if (
                not isinstance(candidate, str)
                or os.sep in candidate
                or "/" in candidate
                or candidate in ("", ".", "..")
            ):
                return None
            return candidate
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _scan_generations(self, job_dir):
        if not os.path.isdir(job_dir):
            return []
        names = [
            name
            for name in os.listdir(job_dir)
            if name.startswith(_CP_PREFIX) and name.endswith(_CP_SUFFIX)
        ]
        names.sort(reverse=True)  # 零填充编号，字典序即世代新旧序
        return names

    def _load_latest(self, job, job_dir):
        """返回 (payload, rollback, rollback_from)。

        优先用指针声明的当前世代；其损坏则从新到旧扫描世代文件，取第一个有效点。
        """
        candidate = self._read_pointer(job_dir)
        if candidate is not None:
            try:
                return self._load_file(os.path.join(job_dir, candidate), job), False, None
            except (OSError, ValueError, UnicodeError, CheckpointError):
                pass  # 当前点损坏，落入回退扫描

        rollback_from = candidate or _POINTER
        for name in self._scan_generations(job_dir):
            if name == candidate:
                continue
            try:
                payload = self._load_file(os.path.join(job_dir, name), job)
                logger.warning(
                    "job=%s 当前检查点 %s 损坏，回退到上一有效点 version=%s",
                    job,
                    rollback_from,
                    payload.get("version"),
                )
                return payload, True, rollback_from
            except (OSError, ValueError, UnicodeError, CheckpointError):
                continue
        return None, candidate is not None or os.path.exists(
            os.path.join(job_dir, _POINTER)
        ), rollback_from

    # -- 写入 ------------------------------------------------------------

    def save(self, job, stage, state, elapsed=None, complete=True):
        """保存阶段检查点；任何失败都抛出异常且不推进阶段。

        ``complete=True``（默认）：标记阶段完成，进入 completed 前缀；
        ``complete=False``：只推进当前未完成阶段的抓取位点，阶段仍可继续运行。
        """
        if stage not in self._stages:
            raise StageDependencyError("未知阶段 %r，合法阶段：%s" % (stage, self._stages))
        if not isinstance(state, dict):
            raise CheckpointError("state 必须是 dict")
        if elapsed is not None and not isinstance(elapsed, (int, float)):
            raise CheckpointError("elapsed 必须是数值")

        job_dir = self._job_dir(job)
        with self._locked(job):
            previous, _, _ = self._load_latest(job, job_dir)
            completed = list(previous["completed_stages"]) if previous else []
            stage_states = dict(previous["stages"]) if previous else {}
            last_version = previous["version"] if previous else 0

            idx = self._stages.index(stage)
            if idx > len(completed):
                raise StageDependencyError(
                    "阶段 %r 依赖 %r 先完成，已完成：%s"
                    % (stage, self._stages[idx - 1], completed)
                )
            if idx < len(completed) - 1:
                raise StageDependencyError(
                    "阶段 %r 的后继阶段已完成，不能逆序保存" % stage
                )
            if complete:
                # 完成点：只能完成第一个未完成阶段，或重存刚完成的当前阶段
                if idx > len(completed):
                    raise StageDependencyError("不能跨阶段标记完成")
                if stage not in completed:
                    completed.append(stage)
            else:
                # 位点点：只能落在第一个未完成阶段，已完成阶段不允许再写位点
                if idx != len(completed):
                    raise StageDependencyError(
                        "阶段 %r 已完成或不是当前阶段，位点只能写入首个未完成阶段" % stage
                    )

            # 先序列化，序列化失败时磁盘完全不动，阶段自然不推进
            position = state.get("position")
            entry = {
                "version": last_version + 1,
                "position": position,
                "state": state,
                "elapsed": elapsed,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            stage_states[stage] = entry
            payload = {
                "format_version": FORMAT_VERSION,
                "job": job,
                "stage": stage,
                "stage_complete": complete,
                "version": last_version + 1,
                "completed_stages": completed,
                "stages": stage_states,
                "position": position,
                "state": state,
                "elapsed": elapsed,
                "saved_at": entry["saved_at"],
            }
            # 先序列化（含校验和计算），失败时磁盘完全不动，阶段自然不推进
            try:
                payload["checksum"] = _checksum(payload)
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise CheckpointError("状态无法序列化为 JSON：%s" % exc) from exc

            generation = last_version + 1
            cp_name = "%s%06d%s" % (_CP_PREFIX, generation, _CP_SUFFIX)
            self._sweep_tmp(job_dir)
            try:
                _atomic_write(os.path.join(job_dir, cp_name), data)
                pointer = {"file": cp_name, "checksum": payload["checksum"]}
                _atomic_write(
                    os.path.join(job_dir, _POINTER),
                    json.dumps(pointer, ensure_ascii=False).encode("utf-8"),
                )
            except OSError as exc:
                # 指针未更新，resume 仍指向上一有效点，阶段不推进
                raise CheckpointError("检查点写入失败，阶段 %s 未推进：%s" % (stage, exc)) from exc

            self._prune(job_dir, cp_name)
            elapsed_text = "n/a" if elapsed is None else "%.3fs" % elapsed
            logger.info(
                "job=%s stage=%s version=%d complete=%s position=%r elapsed=%s checksum=%s",
                job,
                stage,
                generation,
                complete,
                position,
                elapsed_text,
                payload["checksum"],
            )
            return {
                "job": job,
                "stage": stage,
                "saved": True,
                "stage_complete": complete,
                "version": generation,
                "position": position,
                "completed_stages": list(completed),
                "checksum": payload["checksum"],
                "file": cp_name,
            }

    def _sweep_tmp(self, job_dir):
        with contextlib.suppress(OSError):
            for name in os.listdir(job_dir):
                if name.endswith(".tmp"):
                    os.unlink(os.path.join(job_dir, name))

    def _prune(self, job_dir, current_name):
        """只保留当前点及最近若干世代，裁剪失败不影响保存结果。"""
        with contextlib.suppress(OSError):
            names = self._scan_generations(job_dir)
            for name in names[_RETENTION:]:
                if name != current_name:
                    os.unlink(os.path.join(job_dir, name))

    # -- 恢复 ------------------------------------------------------------

    def resume(self, job):
        """只读恢复，重复调用（含跨进程/新实例）返回完全一致的结果。"""
        job_dir = self._job_dir(job)
        with self._locked(job):
            payload, rollback, rollback_from = self._load_latest(job, job_dir)
            if payload is None:
                result = self._fresh_result(job, rollback, rollback_from)
                logger.info(
                    "job=%s 无有效检查点，从 %s 开始（rollback=%s）",
                    job,
                    self._stages[0],
                    rollback,
                )
                return result

            completed = list(payload["completed_stages"])
            if payload.get("stage_complete"):
                # 完成点：从 completed 前缀之后继续
                next_stage = (
                    self._stages[len(completed)]
                    if len(completed) < len(self._stages)
                    else None
                )
            else:
                # 位点点：当前阶段还没跑完，回到该阶段继续
                next_stage = payload["stage"]
            stored_pages = set()
            for entry in payload["stages"].values():
                pages = entry.get("state", {}).get("pages")
                if isinstance(pages, list):
                    for page in pages:
                        if isinstance(page, (str, int)) and not isinstance(page, bool):
                            stored_pages.add(page)
            result = {
                "job": job,
                "stage": next_stage,
                "restored": True,
                "finished": next_stage is None,
                "version": payload["version"],
                "stage_complete": payload.get("stage_complete", True),
                "position": payload.get("position"),
                "state": payload.get("state", {}),
                "stages": payload["stages"],
                "completed": completed,
                "skipped": list(completed),
                "stored_pages": sorted(stored_pages),
                "rollback": rollback,
                "rollback_from": rollback_from,
                "checksum": payload["checksum"],
            }
            logger.info(
                "job=%s resume stage=%s version=%d restored=True rollback=%s skipped=%s",
                job,
                next_stage,
                payload["version"],
                rollback,
                completed,
            )
            return result

    def _fresh_result(self, job, rollback=False, rollback_from=None):
        return {
            "job": job,
            "stage": self._stages[0],
            "restored": False,
            "finished": False,
            "version": 0,
            "stage_complete": False,
            "position": None,
            "state": {},
            "stages": {},
            "completed": [],
            "skipped": [],
            "stored_pages": [],
            "rollback": rollback,
            "rollback_from": rollback_from,
            "checksum": None,
        }

    # -- 阶段计时上下文 --------------------------------------------------

    @contextlib.contextmanager
    def stage(self, job, stage_name):
        """阶段计时上下文：正常退出自动 save 并记录耗时，抛异常则不推进阶段。"""
        if stage_name not in self._stages:
            raise StageDependencyError("未知阶段 %r" % (stage_name,))
        job_dir = self._job_dir(job)
        with self._locked(job):
            previous, _, _ = self._load_latest(job, job_dir)
            completed = list(previous["completed_stages"]) if previous else []
            if len(completed) >= len(self._stages):
                raise CheckpointError("任务 %s 已完成全部阶段" % job)
            expected = self._stages[len(completed)]
            if expected != stage_name:
                raise StageDependencyError(
                    "任务 %s 下一阶段是 %r，不能进入 %r" % (job, expected, stage_name)
                )
        context = _StageContext(job, stage_name)
        start = time.perf_counter()
        logger.info("job=%s enter stage=%s", job, stage_name)
        try:
            yield context
        except BaseException:
            logger.exception(
                "job=%s stage=%s 执行失败，不写检查点、不推进阶段", job, stage_name
            )
            raise
        elapsed = time.perf_counter() - start
        self.save(job, stage_name, context.state, elapsed=elapsed)

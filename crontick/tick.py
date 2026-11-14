"""采集定时触发：租约互斥、启动错峰、错过补偿、任务级时区。

同一任务同一时刻只允许一个实例触发（触发前抢租约）；启动时间在
jitter_window 内按实例确定性地错开；实例恢复后按最近一次应触发时间
补跑一次，不重复补；slot 按任务时区的本地零点对齐。
"""
import hashlib
import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

DEFAULT_TASKS = {"shop": {"interval": 60, "tz": "UTC", "jitter_window": 0}}


class CronTickError(Exception):
    """crontick 所有异常的基类。"""


class ScheduleError(CronTickError):
    """任务配置或时间参数非法。"""


class LeaseError(CronTickError):
    """租约或触发记录读写失败。"""


def _aware_utc(now):
    """归一化为带时区的 UTC 时间；naive 时间按 UTC 处理（兼容旧调用）。"""
    if now is None:
        return datetime.now(UTC)
    if not isinstance(now, datetime):
        raise ScheduleError(f"now 必须是 datetime，收到 {type(now).__name__}")
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


class InMemoryStore:
    """进程内租约与触发记录，线程安全，供单进程多实例或测试使用。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._leases = {}  # (task, slot) -> (instance_id, expire_epoch)
        self._fired = {}   # task -> record dict

    def acquire(self, task, slot, instance_id, ttl):
        key = (task, slot)
        with self._lock:
            holder = self._leases.get(key)
            if holder and holder[1] > time.time():
                return False
            self._leases[key] = (instance_id, time.time() + ttl)
            return True

    def get_fired(self, task):
        with self._lock:
            return self._fired.get(task)

    def set_fired(self, task, record):
        with self._lock:
            self._fired[task] = record


class FileStore:
    """共享文件系统上的租约与触发记录，供多机多实例互斥。

    租约用 O_CREAT|O_EXCL 原子创建，内含实例与过期时间；实例崩溃
    留下的租约在 TTL 到期后被后来者清除。触发记录写 state.json
    （临时文件 + os.replace 原子替换）。
    """

    def __init__(self, directory):
        self.dir = os.fspath(directory)
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as e:
            raise LeaseError(f"无法创建租约目录 {self.dir}: {e}") from e

    def _lease_path(self, task, slot):
        digest = hashlib.sha1(f"{task}:{slot}".encode()).hexdigest()[:16]
        return os.path.join(self.dir, f"lease-{digest}.json")

    def _state_path(self):
        return os.path.join(self.dir, "state.json")

    def acquire(self, task, slot, instance_id, ttl):
        path = self._lease_path(task, slot)
        payload = json.dumps({"task": task, "slot": slot, "instance": instance_id,
                              "expires": time.time() + ttl})
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    with open(path, encoding="utf-8") as f:
                        holder = json.load(f)
                except (OSError, ValueError):
                    holder = None  # 读不到或已损坏，按过期租约处理
                if holder and holder.get("expires", 0) > time.time():
                    return False
                try:
                    os.unlink(path)
                except OSError:
                    return False
                continue
            except OSError as e:
                raise LeaseError(f"租约创建失败 {path}: {e}") from e
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            return True
        return False

    def get_fired(self, task):
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                return json.load(f).get(task)
        except FileNotFoundError:
            return None
        except ValueError as e:
            raise LeaseError(f"状态文件损坏 {self._state_path()}: {e}") from e
        except OSError as e:
            raise LeaseError(f"状态文件读取失败 {self._state_path()}: {e}") from e

    def set_fired(self, task, record):
        state = {}
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            pass
        except ValueError as e:
            raise LeaseError(f"状态文件损坏 {self._state_path()}: {e}") from e
        except OSError as e:
            raise LeaseError(f"状态文件读取失败 {self._state_path()}: {e}") from e
        state[task] = record
        tmp = self._state_path() + f".{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self._state_path())
        except OSError as e:
            raise LeaseError(f"状态文件写入失败 {self._state_path()}: {e}") from e


class Scheduler:
    """带租约、错峰和补偿的触发调度器。

    tasks: {名称: {"interval": 秒, "tz": IANA 时区, "jitter_window": 秒,
                   "lease_ttl": 秒（可选，默认等于 interval）}}
    """

    def __init__(self, tasks=None, store=None, instance_id=None):
        self.tasks = {name: self._validate(name, cfg)
                      for name, cfg in (tasks or DEFAULT_TASKS).items()}
        self.store = store or InMemoryStore()
        self.instance_id = instance_id or f"{socket.gethostname()}-{os.getpid()}"

    @staticmethod
    def _validate(name, cfg):
        interval = cfg.get("interval", 60)
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            raise ScheduleError(f"任务 {name} 的 interval 必须为正数秒，收到 {interval!r}")
        tzname = cfg.get("tz", "UTC")
        try:
            tz = ZoneInfo(tzname)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ScheduleError(f"任务 {name} 的时区无法识别: {tzname!r}") from e
        window = cfg.get("jitter_window", 0)
        if not isinstance(window, (int, float)) or isinstance(window, bool) or window < 0:
            raise ScheduleError(f"任务 {name} 的 jitter_window 必须为非负秒数，收到 {window!r}")
        lease_ttl = cfg.get("lease_ttl") or interval
        if lease_ttl <= 0:
            raise ScheduleError(f"任务 {name} 的 lease_ttl 必须为正数秒，收到 {lease_ttl!r}")
        return {"interval": interval, "tz": tz, "jitter_window": window,
                "lease_ttl": lease_ttl}

    def _require_task(self, task):
        if task not in self.tasks:
            raise ScheduleError(f"未知任务 {task!r}，已配置: {sorted(self.tasks)}")

    def _slot(self, task, now_utc):
        """now 所在 slot 的起始时刻（UTC），按任务时区的本地零点对齐。"""
        cfg = self.tasks[task]
        local = now_utc.astimezone(cfg["tz"])
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        k = int((local - midnight).total_seconds() // cfg["interval"])
        return (midnight + timedelta(seconds=k * cfg["interval"])).astimezone(UTC)

    def _jitter(self, task, slot):
        """实例在窗口内的确定性偏移，同一实例同一 slot 结果稳定。"""
        window = self.tasks[task]["jitter_window"]
        if window <= 0:
            return 0
        key = f"{self.instance_id}:{task}:{slot.isoformat()}".encode()
        return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % int(window)

    def next_run(self, task, now=None):
        """下一次触发时刻（含本实例的错峰偏移），严格大于 now。

        now 为 naive 时按 UTC 计算并返回 naive 时间（兼容旧调用）。
        """
        self._require_task(task)
        naive = now is not None and now.tzinfo is None
        now_utc = _aware_utc(now)
        slot = self._slot(task, now_utc)
        fire = slot + timedelta(seconds=self._jitter(task, slot))
        if fire <= now_utc:
            nxt = self._slot(task, slot + timedelta(seconds=self.tasks[task]["interval"]))
            fire = nxt + timedelta(seconds=self._jitter(task, nxt))
        return fire.replace(tzinfo=None) if naive else fire

    def tick(self, now, run, task="shop"):
        """到点且抢到租约就调 run(task=...)，否则返回跳过原因。

        返回 dict：ran、reason（fired/catchup/jitter_wait/already_fired/
        lease_held/clock_skew）、slot、fire_at、catchup、missed_slots。
        """
        self._require_task(task)
        now_utc = _aware_utc(now)
        cfg = self.tasks[task]
        slot = self._slot(task, now_utc)
        fire_at = slot + timedelta(seconds=self._jitter(task, slot))
        result = {"task": task, "instance": self.instance_id, "slot": slot,
                  "fire_at": fire_at, "ran": False, "reason": None,
                  "catchup": False, "missed_slots": 0}
        if now_utc < fire_at:
            result["reason"] = "jitter_wait"
            return result
        last = self.store.get_fired(task)
        if last and last["slot"] == slot.isoformat():
            result["reason"] = "already_fired"
            return result
        missed = 0
        if last:
            last_slot = datetime.fromisoformat(last["slot"])
            if last_slot > slot:
                result["reason"] = "clock_skew"
                return result
            missed = max(0, int((slot - last_slot).total_seconds()
                                // cfg["interval"]) - 1)
        catchup = missed > 0
        if not self.store.acquire(task, slot.isoformat(), self.instance_id,
                                  cfg["lease_ttl"]):
            result["reason"] = "lease_held"
            return result
        try:
            run(task=task)
        except CronTickError:
            raise
        except Exception as e:
            raise CronTickError(
                f"任务 {task} 在 {slot.isoformat()} 的触发回调失败: {e}") from e
        self.store.set_fired(task, {"slot": slot.isoformat(),
                                    "instance": self.instance_id,
                                    "fired_at": now_utc.isoformat(),
                                    "catchup": catchup, "missed_slots": missed})
        result.update(ran=True, reason="catchup" if catchup else "fired",
                      catchup=catchup, missed_slots=missed)
        return result


def next_run(task, now=None):
    """模块级便捷接口：无状态默认调度器，配置见 DEFAULT_TASKS。"""
    return Scheduler().next_run(task, now)


def tick(now, run, task="shop"):
    """模块级便捷接口：无状态默认调度器，每次调用独立触发。"""
    return Scheduler().tick(now, run, task=task)

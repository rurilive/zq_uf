"""多租户采集执行层：配置、队列、结果目录和配额按租户隔离。"""
import json
import os
import re
import threading
import time

from . import engine

RESULTS = "results"

DEFAULT_CONFIG = {"workers": 4, "quota": 1000, "rate_per_sec": 100}

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TenantError(Exception):
    """tenantcrawl 全部错误的基类。"""


class InvalidTenantError(TenantError, ValueError):
    """租户名为空、含路径分隔符或 ..。"""


class TenantAccessError(TenantError, PermissionError):
    """读取路径越出本租户结果目录。"""


class QuotaExceededError(TenantError, RuntimeError):
    """单次 run 的任务数超出租户配额。"""


class InvalidTaskError(TenantError, ValueError):
    """任务列表不可迭代，或任务不是可 JSON 序列化的 dict。"""


class RateLimiter:
    """每租户一个的限速器，限制任务启动速率（任务/秒）。"""

    def __init__(self, rate_per_sec):
        self._interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if wait > 0:
            time.sleep(wait)


class TenantContext:
    """单个租户的配置、限速器、执行锁和累计统计。"""

    def __init__(self, name):
        self.name = name
        self.config = dict(DEFAULT_CONFIG)
        self.rate_limiter = RateLimiter(self.config["rate_per_sec"])
        self.lock = threading.Lock()
        self.tasks_completed = 0
        self.elapsed_total = 0.0

    @property
    def results_dir(self):
        return os.path.join(RESULTS, self.name)


_CONTEXTS = {}
_REGISTRY_LOCK = threading.Lock()


def validate_tenant(tenant):
    """校验租户名，非法时抛 InvalidTenantError。"""
    if not isinstance(tenant, str) or not tenant:
        raise InvalidTenantError("租户名不能为空")
    if not _TENANT_RE.match(tenant) or ".." in tenant:
        raise InvalidTenantError(f"非法租户名: {tenant!r}")
    return tenant


def _get_context(tenant):
    with _REGISTRY_LOCK:
        context = _CONTEXTS.get(tenant)
        if context is None:
            context = _CONTEXTS[tenant] = TenantContext(tenant)
        return context


def configure(tenant, **overrides):
    """登记或更新租户配置，合法键：workers、quota、rate_per_sec。"""
    context = _get_context(validate_tenant(tenant))
    for key, value in overrides.items():
        if key not in DEFAULT_CONFIG:
            raise ValueError(f"未知配置项: {key!r}")
        if key == "workers" and (not isinstance(value, int) or value < 1):
            raise ValueError(f"workers 必须是 >= 1 的整数: {value!r}")
        if key == "quota" and (not isinstance(value, int) or value < 0):
            raise ValueError(f"quota 必须是 >= 0 的整数: {value!r}")
        if key == "rate_per_sec" and (
            not isinstance(value, (int, float)) or value <= 0
        ):
            raise ValueError(f"rate_per_sec 必须 > 0: {value!r}")
    with context.lock:
        context.config.update(overrides)
        if "rate_per_sec" in overrides:
            context.rate_limiter = RateLimiter(overrides["rate_per_sec"])
    return dict(context.config)


def run(tenant, tasks):
    """执行租户任务，结果写入 results/<tenant>/out.json，返回完成任务数。"""
    context = _get_context(validate_tenant(tenant))
    task_list = _validate_tasks(tasks)
    quota = context.config["quota"]
    if len(task_list) > quota:
        raise QuotaExceededError(
            f"租户 {tenant} 单次配额 {quota}，收到 {len(task_list)} 个任务"
        )
    with context.lock:
        started = time.monotonic()
        rows = engine.execute(context, task_list)
        _append_rows(context, rows)
        context.tasks_completed += len(rows)
        context.elapsed_total += time.monotonic() - started
    return len(rows)


def read_results(tenant, name="out.json"):
    """读取本租户结果；任何越出租户目录的路径都抛 TenantAccessError。"""
    validate_tenant(tenant)
    base = os.path.realpath(os.path.join(RESULTS, tenant))
    path = os.path.realpath(os.path.join(base, name))
    if path != base and not path.startswith(base + os.sep):
        raise TenantAccessError(f"租户 {tenant} 无权读取: {name!r}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"租户 {tenant} 没有结果文件: {name}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_all(path=None):
    """运维聚合视图：合并所有租户的结果；给定 path 时只读该文件。"""
    if path is not None:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    rows = []
    if not os.path.isdir(RESULTS):
        return rows
    for tenant in sorted(os.listdir(RESULTS)):
        candidate = os.path.join(RESULTS, tenant, "out.json")
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as handle:
                rows.extend(json.load(handle))
    return rows


def stats(tenant):
    """返回每租户的任务数与资源占用。"""
    context = _get_context(validate_tenant(tenant))
    with context.lock:
        return {
            "tenant": tenant,
            "tasks_completed": context.tasks_completed,
            "elapsed_total": round(context.elapsed_total, 6),
            "workers": context.config["workers"],
            "quota": context.config["quota"],
            "rate_per_sec": context.config["rate_per_sec"],
            "results_dir": context.results_dir,
        }


def reset():
    """清空租户注册表（测试与 demo 用）。"""
    with _REGISTRY_LOCK:
        _CONTEXTS.clear()


def _validate_tasks(tasks):
    try:
        task_list = list(tasks)
    except TypeError as exc:
        raise InvalidTaskError(f"tasks 必须是可迭代对象: {exc}") from exc
    for task in task_list:
        if not isinstance(task, dict):
            raise InvalidTaskError(f"任务必须是 dict: {task!r}")
        try:
            json.dumps(task)
        except (TypeError, ValueError) as exc:
            raise InvalidTaskError(f"任务无法序列化为 JSON: {exc}") from exc
    return task_list


def _append_rows(context, rows):
    os.makedirs(context.results_dir, exist_ok=True)
    path = os.path.join(context.results_dir, "out.json")
    existing = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            existing = json.load(handle)
    existing.extend(rows)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, ensure_ascii=False)
    os.replace(tmp_path, path)

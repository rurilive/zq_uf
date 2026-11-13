"""robots 规则的可过期共享缓存。

未调用 ``configure`` 时保持历史行为：每次 ``allowed`` 都直接拉取。
调用 ``configure(cache_path=..., ttl=...)`` 后启用按 host 的文件缓存：
命中直接复用；过期后后台刷新（stale-while-revalidate）；刷新失败沿用
旧规则并通过 logging 记录告警；多进程经 ``fcntl`` 文件锁串行写入，
临时文件 + os.replace 保证缓存文件原子更新。
"""

import fcntl
import json
import logging
import os
import tempfile
import threading
import time

logger = logging.getLogger("robotscache")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

DEFAULT_TTL = 3600.0
DEFAULT_STALE_TTL = 86400.0
DEFAULT_LOCK_TIMEOUT = 5.0
LOCK_SUFFIX = ".lock"

_fetcher = None
_cache_path = None
_ttl = DEFAULT_TTL
_stale_ttl = DEFAULT_STALE_TTL
_lock_timeout = DEFAULT_LOCK_TIMEOUT
_refresh_lock = threading.Lock()
_refreshing = set()
_threads = []


class RobotsError(Exception):
    """robotscache 统一异常基类，异常路径不允许静默跳过。"""


class RobotsFetchError(RobotsError):
    """拉取 robots 规则失败。"""


class CacheConfigError(RobotsError):
    """缓存参数或缓存文件不合法。"""


def configure(cache_path=None,
              ttl=DEFAULT_TTL,
              stale_ttl=DEFAULT_STALE_TTL,
              lock_timeout=DEFAULT_LOCK_TIMEOUT):
    """启用共享缓存并设定过期阈值。

    参数可显式传入，未传入的项回退到 ROBOTSCACHE_PATH / ROBOTSCACHE_TTL /
    ROBOTSCACHE_STALE_TTL / ROBOTSCACHE_LOCK_TIMEOUT 环境变量；两者都没有
    时保持无缓存的直连模式。
    """
    global _cache_path, _ttl, _stale_ttl, _lock_timeout
    if cache_path is None:
        cache_path = os.environ.get("ROBOTSCACHE_PATH")
    ttl = _as_float("ttl", ttl, "ROBOTSCACHE_TTL")
    stale_ttl = _as_float("stale_ttl", stale_ttl,
                          "ROBOTSCACHE_STALE_TTL")
    lock_timeout = _as_float("lock_timeout", lock_timeout,
                             "ROBOTSCACHE_LOCK_TIMEOUT")
    if ttl < 0 or stale_ttl < 0 or lock_timeout <= 0:
        raise CacheConfigError(
            "ttl/stale_ttl 不能为负，lock_timeout 必须为正数")
    if cache_path:
        directory = os.path.dirname(os.path.abspath(cache_path))
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise CacheConfigError(
                "无法创建缓存目录 %s: %s" % (directory, exc)) from exc
    _cache_path = os.path.abspath(cache_path) if cache_path else None
    _ttl = ttl
    _stale_ttl = stale_ttl
    _lock_timeout = lock_timeout
    return {
        "cache_path": _cache_path,
        "ttl": _ttl,
        "stale_ttl": _stale_ttl,
        "lock_timeout": _lock_timeout,
    }


def _as_float(name, value, env_name):
    if value is None:
        value = os.environ.get(env_name)
    if value is None:
        raise CacheConfigError("参数 %s 缺失" % name)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CacheConfigError("参数 %s 必须是数字: %r" % (name, value)) \
            from exc


def set_fetcher(func):
    """注入 robots 拉取函数，签名为 fetcher(host) -> 规则 dict。"""
    global _fetcher
    if not callable(func):
        raise CacheConfigError("fetcher 必须是可调用对象")
    _fetcher = func


def _require_fetcher():
    if _fetcher is None:
        raise RuntimeError("fetcher 未设置")
    return _fetcher


def _fetch(host):
    fetcher = _require_fetcher()
    try:
        rules = fetcher(host)
    except Exception as exc:
        raise RobotsFetchError(
            "拉取 %s 的 robots 规则失败: %s" % (host, exc)) from exc
    if not isinstance(rules, dict):
        raise RobotsFetchError(
            "fetcher 返回值必须是 dict，实际为 %r" % type(rules).__name__)
    return rules


class _FileLock:
    """基于 fcntl 的跨进程排他锁，带超时，失败时显式抛错。"""

    def __init__(self, path):
        self._lock_path = path + LOCK_SUFFIX
        self._handle = None

    def __enter__(self):
        self._handle = open(self._lock_path, "w")
        deadline = time.monotonic() + _lock_timeout
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise CacheConfigError(
                        "等待缓存文件锁超时(%ss): %s"
                        % (_lock_timeout, self._lock_path))
                time.sleep(0.02)

    def __exit__(self, exc_type, exc, tb):
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def _read_cache():
    """读取共享缓存；文件损坏时备份并视为空缓存，绝不静默忽略。"""
    if not os.path.exists(_cache_path):
        return {}
    try:
        with open(_cache_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        backup = "%s.corrupt-%d" % (_cache_path, time.time_ns())
        try:
            os.replace(_cache_path, backup)
        except OSError:
            backup = "(备份失败)"
        logger.error(
            "缓存文件损坏，已备份到 %s 并按空缓存重建，原因: %s",
            backup, exc)
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), dict):
        logger.error("缓存文件格式非法，按空缓存重建: %s", _cache_path)
        return {}
    return data["hosts"]


def _write_cache(hosts):
    """持锁后以临时文件 + os.replace 原子更新整个缓存。"""
    payload = {"version": 1, "hosts": hosts}
    directory = os.path.dirname(_cache_path) or "."
    with _FileLock(_cache_path):
        current = _read_cache()
        current.update(hosts)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".robotscache-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "hosts": current},
                          handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, _cache_path)
        except OSError as exc:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise CacheConfigError(
                "写入缓存文件失败 %s: %s" % (_cache_path, exc)) from exc


def _store_entry(host, rules, fetched_at):
    entry = {"rules": rules, "fetched_at": fetched_at}
    if _cache_path is not None:
        _write_cache({host: entry})
    return entry


def _get_entry(host):
    if _cache_path is None:
        return None
    try:
        return _read_cache().get(host)
    except CacheConfigError as exc:
        logger.error("读取共享缓存失败: %s", exc)
        raise


def _spawn_refresh(host):
    """同 host 同一时刻只允许一个后台刷新线程。"""
    with _refresh_lock:
        if host in _refreshing:
            return
        _refreshing.add(host)
        thread = threading.Thread(
            target=_refresh_worker, args=(host,), daemon=True)
        thread.start()
        _threads.append(thread)


def _refresh_worker(host):
    try:
        rules = _fetch(host)
        _store_entry(host, rules, time.time())
        logger.info("后台刷新成功 host=%s", host)
    except RobotsError as exc:
        logger.warning("后台刷新失败，沿用旧规则 host=%s: %s", host, exc)
    finally:
        with _refresh_lock:
            _refreshing.discard(host)


def join_refreshes(timeout=5.0):
    """等待本进程已发起的后台刷新结束，主要供测试和退出前同步使用。"""
    for thread in list(_threads):
        if thread.is_alive():
            thread.join(timeout)
    return not any(thread.is_alive() for thread in _threads)


def _host_and_path(url):
    tail = url.split("//", 1)[-1]
    host = tail.split("/", 1)[0]
    if "//" in url:
        path = "/" + tail.split("/", 1)[-1] if "/" in tail else "/"
    else:
        path = url
    return host, path


def _is_allowed(rules, path):
    for prefix in rules.get("disallow", []):
        if path.startswith(prefix):
            return False
    return True


def allowed(url):
    """判断 url 是否允许抓取，接口签名保持不变。"""
    host, path = _host_and_path(url)
    if _cache_path is None:
        rules = _fetch(host)
        logger.info("直连拉取 host=%s", host)
        return _is_allowed(rules, path)

    entry = _get_entry(host)
    now = time.time()
    if entry is None:
        rules = _fetch(host)
        _store_entry(host, rules, now)
        logger.info("缓存未命中，同步拉取 host=%s", host)
        return _is_allowed(rules, path)

    age = now - entry.get("fetched_at", 0)
    if age < _ttl:
        logger.info("缓存命中 host=%s age=%.3fs", host, age)
        return _is_allowed(entry["rules"], path)

    if age > _ttl + _stale_ttl:
        rules = _fetch(host)
        _store_entry(host, rules, now)
        logger.warning(
            "缓存超过 stale_ttl，强制同步刷新 host=%s age=%.3fs", host, age)
        return _is_allowed(rules, path)

    logger.info(
        "缓存过期，后台刷新并先沿用旧规则 host=%s age=%.3fs", host, age)
    _spawn_refresh(host)
    return _is_allowed(entry["rules"], path)


def refresh(host):
    """显式刷新指定 host 的规则并落盘，返回最新规则。"""
    rules = _fetch(host)
    if _cache_path is None:
        logger.info("直连刷新 host=%s", host)
        return rules
    _store_entry(host, rules, time.time())
    logger.info("显式刷新成功 host=%s", host)
    return rules

"""站点预检：并发探测、按站点缓存（带过期）、临时/永久失败分流、探测项与结论落盘。"""

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import urlparse

OK = "ok"
TEMPORARY = "temporary"
PERMANENT = "permanent"
_VERDICTS = (OK, TEMPORARY, PERMANENT)

_DEFAULTS = {
    "state_dir": None,   # 落盘目录；None 表示只缓存于内存
    "ttl": 300.0,        # 通过结果的缓存秒数，过期后旧结论先返回、后台刷新
    "temp_ttl": 60.0,    # 临时失败后多少秒允许同步重试
    "perm_ttl": None,    # 永久失败的缓存秒数；None 表示不过期（直接拦截）
    "timeout": 5.0,      # 单站点超时秒数，超时记临时失败，不影响整批
    "prober": None,      # 自定义探测函数 f(site, timeout) -> verdict | (verdict, reason) | dict
}

_lock = threading.RLock()
_cache = {}              # site -> {"verdict", "reason", "checked_at", "expires"}
_changes = []            # 结论变更记录（内存副本，落盘见 changes.jsonl）
_config = dict(_DEFAULTS)
_progress = None         # 当前批量进度（落盘见 progress.json）
_refresh_queue = queue.Queue()
_refresh_thread = None


# ---------------------------------------------------------------- 探测

def _default_prober(site, timeout):
    """默认探测：HTTP HEAD。返回 (verdict, reason, items)。"""
    url = site if "://" in site else "http://" + site
    items = []
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "crawlprobe2/2.0"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError) as exc:
        detail = getattr(exc, "reason", exc)
        items.append({"item": "connect", "ok": False, "detail": str(detail)})
        return TEMPORARY, "network error: %s" % detail, items
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    items.append({"item": "http_status", "ok": status < 400,
                  "detail": "HTTP %s" % status})
    items.append({"item": "latency", "ok": True, "detail": "%sms" % elapsed_ms})
    if status < 400:
        return OK, "HTTP %s" % status, items
    if status in (408, 429) or status >= 500:
        return TEMPORARY, "HTTP %s (server/throttled, retryable)" % status, items
    return PERMANENT, "HTTP %s (client error, permanent)" % status, items


def _normalize(raw):
    """自定义 prober 的返回值统一为 (verdict, reason, items)。"""
    if isinstance(raw, dict):
        parts = (raw.get("verdict", OK), raw.get("reason", ""), raw.get("items", []))
    elif isinstance(raw, (tuple, list)):
        parts = (tuple(raw) + ("", [], ""))[:3]
    else:
        parts = (raw, "", [])
    verdict_, reason, items = parts
    if verdict_ not in _VERDICTS:
        verdict_, reason = TEMPORARY, "bad prober verdict: %r" % (verdict_,)
    return verdict_, reason, items


def _probe_site(site, timeout):
    """执行一次探测并记录；任何异常都记为临时失败。"""
    started = time.monotonic()
    try:
        prober = _config["prober"] or _default_prober
        verdict_, reason, items = _normalize(prober(site, timeout))
    except Exception as exc:  # 探测函数自身异常按临时失败处理
        verdict_, reason, items = TEMPORARY, "prober error: %s" % exc, []
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    _record(site, verdict_, reason, items, duration_ms)


# ---------------------------------------------------------------- 记录与落盘

def _ttl_for(verdict_):
    if verdict_ == OK:
        return _config["ttl"]
    if verdict_ == TEMPORARY:
        return _config["temp_ttl"]
    return _config["perm_ttl"]


def _record(site, verdict_, reason, items=None, duration_ms=None):
    now = time.time()
    with _lock:
        old = _cache.get(site)
        old_verdict = old["verdict"] if old else None
        ttl = _ttl_for(verdict_)
        _cache[site] = {
            "verdict": verdict_,
            "reason": reason,
            "checked_at": now,
            "expires": None if ttl is None else now + ttl,
        }
        if old_verdict is not None and old_verdict != verdict_:
            change = {"ts": now, "site": site, "old": old_verdict,
                      "new": verdict_, "reason": reason}
            _changes.append(change)
            _append_jsonl("changes.jsonl", change)
        _append_jsonl("probes.jsonl", {
            "ts": now, "site": site, "verdict": verdict_, "reason": reason,
            "duration_ms": duration_ms, "items": items or [],
        })
        _save_verdicts_locked()


def _state_path(name):
    state_dir = _config["state_dir"]
    return None if state_dir is None else os.path.join(state_dir, name)


def _append_jsonl(name, obj):
    path = _state_path(name)
    if path is None:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _save_verdicts_locked():
    path = _state_path("verdicts.json")
    if path is None:
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def _progress_update(site_done=None, completed=None):
    global _progress
    with _lock:
        if _progress is None:
            return
        if site_done is not None:
            _progress["done"].append(site_done)
        if completed is not None:
            _progress["completed"] = completed
        _progress["updated"] = time.time()
        path = _state_path("progress.json")
        if path is None:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(_progress, fh, ensure_ascii=False, indent=1)
        except OSError:
            pass


# ---------------------------------------------------------------- 后台刷新

def _enqueue_refresh(site):
    global _refresh_thread
    with _lock:
        if _refresh_thread is None or not _refresh_thread.is_alive():
            _refresh_thread = threading.Thread(
                target=_refresh_loop, name="crawlprobe2-refresh", daemon=True)
            _refresh_thread.start()
    _refresh_queue.put(site)


def _refresh_loop():
    while True:
        site = _refresh_queue.get()
        try:
            _probe_site(site, _config["timeout"])
        finally:
            _refresh_queue.task_done()


def wait_idle(timeout=10.0):
    """等待后台刷新全部完成（演示与测试用）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _refresh_queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- 对外接口

def _domain(site):
    text = site if "://" in site else "//" + site
    parsed = urlparse(text)
    host = (parsed.netloc or parsed.path).split("@")[-1].split(":")[0]
    return host.strip().lower() or site


def _row(site, entry, cached, stale=False):
    return {
        "site": site,
        "ok": entry["verdict"] == OK,
        "cached": cached,
        "stale": stale,
        "verdict": entry["verdict"],
        "reason": entry["reason"],
        "domain": _domain(site),
    }


def probe(sites, workers=1):
    """并发探测一批站点。

    返回 {"results": [...], "cache_hits": n, "ok"/"temporary"/"permanent": n,
    "interrupted": bool, "remaining": [...]}。缓存命中（含过期旧结论与永久
    失败拦截）不再实探；临时失败到期后同步重试；单站点超时记临时失败，
    不影响整批；键盘中断时已完成的站点落盘，重跑即续跑。
    """
    global _progress
    now = time.time()
    results = [None] * len(sites)
    todo = {}  # site -> [indexes]
    hits = 0
    with _lock:
        for i, site in enumerate(sites):
            entry = _cache.get(site)
            if entry is None:
                todo.setdefault(site, []).append(i)
            elif entry["expires"] is None or entry["expires"] > now:
                results[i] = _row(site, entry, cached=True)
                hits += 1
            elif entry["verdict"] == TEMPORARY:
                todo.setdefault(site, []).append(i)  # 临时失败到期：同步重试
            else:
                # 通过/永久失败过期：先返回旧结论，后台刷新
                results[i] = _row(site, entry, cached=True, stale=True)
                hits += 1
                _enqueue_refresh(site)

    interrupted = False
    remaining = []
    if todo:
        timeout = _config["timeout"]
        with _lock:
            _progress = {"sites": list(todo), "done": [],
                         "completed": False, "updated": time.time()}
        _progress_update()
        executor = ThreadPoolExecutor(max_workers=max(1, int(workers)))
        futures = {}
        for site in todo:
            futures[executor.submit(_probe_site, site, timeout)] = (
                site, time.monotonic() + timeout)
        try:
            for future, (site, deadline) in futures.items():
                try:
                    future.result(timeout=max(0.0, deadline - time.monotonic()))
                except FutureTimeout:
                    _record(site, TEMPORARY, "timeout after %.1fs" % timeout)
                _progress_update(site_done=site)
        except KeyboardInterrupt:
            interrupted = True
            remaining = [s for f, (s, _) in futures.items() if not f.done()]
        finally:
            executor.shutdown(wait=False)
            _progress_update(completed=not interrupted)

    with _lock:
        for site, indexes in todo.items():
            if site in remaining:
                row = {"site": site, "ok": False, "cached": False, "stale": False,
                       "verdict": "unknown", "reason": "interrupted",
                       "domain": _domain(site)}
            else:
                row = _row(site, _cache[site], cached=False)
            for i in indexes:
                results[i] = row

    counts = {OK: 0, TEMPORARY: 0, PERMANENT: 0}
    for row in results:
        if row["verdict"] in counts:
            counts[row["verdict"]] += 1
    return {
        "results": results,
        "cache_hits": hits,
        "ok": counts[OK],
        "temporary": counts[TEMPORARY],
        "permanent": counts[PERMANENT],
        "interrupted": interrupted,
        "remaining": remaining,
    }


def verdict(site):
    """单站结论；未知站点按通过处理。永久失败时 reason 为判定依据。"""
    with _lock:
        entry = _cache.get(site)
        if entry is None:
            return {"site": site, "verdict": OK, "reason": ""}
        return {"site": site, "verdict": entry["verdict"],
                "reason": entry["reason"], "checked_at": entry["checked_at"]}


def aggregate(results):
    """按域名聚合 probe() 的结果（接受返回值或其 results 列表）。"""
    rows = results.get("results") if isinstance(results, dict) else results
    out = {}
    for row in rows:
        domain = row.get("domain") or _domain(row["site"])
        bucket = out.setdefault(domain, {"domain": domain, "total": 0, "ok": 0,
                                         "temporary": 0, "permanent": 0,
                                         "cached": 0})
        bucket["total"] += 1
        if row["verdict"] in (OK, TEMPORARY, PERMANENT):
            bucket[row["verdict"]] += 1
        if row.get("cached"):
            bucket["cached"] += 1
    return out


def changes():
    """结论变更记录（与 changes.jsonl 内容一致）。"""
    with _lock:
        return [dict(c) for c in _changes]


def configure(**options):
    """更新配置。state_dir 变化时从该目录重新加载结论缓存（中断续跑）。"""
    with _lock:
        for key in options:
            if key not in _DEFAULTS:
                raise KeyError("unknown option: %s" % key)
        reload_dir = ("state_dir" in options
                      and options["state_dir"] != _config["state_dir"])
        _config.update(options)
        if reload_dir:
            _cache.clear()
            state_dir = _config["state_dir"]
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)
                path = os.path.join(state_dir, "verdicts.json")
                if os.path.exists(path):
                    try:
                        with open(path, encoding="utf-8") as fh:
                            _cache.update(json.load(fh))
                    except (OSError, ValueError):
                        pass


def reset():
    """清空内存缓存、变更记录并恢复默认配置（主要供测试）。"""
    global _progress
    with _lock:
        _cache.clear()
        del _changes[:]
        _config.clear()
        _config.update(_DEFAULTS)
        _progress = None

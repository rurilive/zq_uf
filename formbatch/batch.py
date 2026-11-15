"""幂等批量表单提交。

- 每条表单带幂等键，成功项在键保留期内不重提（跳过）；
- 站点级 + 全局两级限速，支持并发提交；
- 失败项按类别单独重试，重试次数有上限；
- 结果逐条对账，可导出明细，异常带请求序号。
"""

from __future__ import annotations

import csv
import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

# 失败类别
CATEGORY_NETWORK = "network"          # 网络错误，可重试
CATEGORY_TIMEOUT = "timeout"          # 超时，可重试
CATEGORY_RATE_LIMITED = "rate_limited"  # 被服务端限速（如 429），可重试
CATEGORY_SERVER = "server"            # 服务端 5xx，可重试
CATEGORY_CLIENT = "client"            # 客户端 4xx，不重试
CATEGORY_UNKNOWN = "unknown"          # 未分类异常，可重试

RETRYABLE_CATEGORIES = frozenset({
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    CATEGORY_RATE_LIMITED,
    CATEGORY_SERVER,
    CATEGORY_UNKNOWN,
})

DEFAULTS = {
    "global_rate": None,        # 全局每秒请求数，None 不限
    "site_rate": None,          # 单站点每秒请求数，None 不限
    "max_workers": 4,           # 并发线程数
    "max_retries": 3,           # 首次尝试之外的最多重试次数
    "idempotency_ttl": 3600.0,  # 批次结束后幂等键保留秒数
    "backoff_base": 0.5,        # 重试退避基数（秒），按次数线性放大
}

_CONFIG = dict(DEFAULTS)
_LOCK = threading.RLock()
_BATCHES = {}   # batch_id -> 批次记录
_REGISTRY = {}  # 幂等键 -> {"batch": batch_id, "expires": 时间戳}


class SubmitError(Exception):
    """sender 抛出的提交异常，带失败类别和请求序号。"""

    def __init__(self, message, *, category=CATEGORY_UNKNOWN, seq=None):
        super().__init__(message)
        self.category = category
        self.seq = seq


class RateLimiter:
    """站点 + 全局两层限速：预约下一次允许发起的时间，到点前睡眠。"""

    def __init__(self, global_rate=None, site_rate=None, *,
                 clock=time.monotonic, sleep=time.sleep):
        self._global_interval = 1.0 / global_rate if global_rate else 0.0
        self._site_interval = 1.0 / site_rate if site_rate else 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_global = 0.0
        self._next_site = {}
        self.waits = 0
        self.wait_seconds = 0.0

    def acquire(self, site):
        """阻塞直到允许向 site 发起一次请求，返回等待秒数。"""
        with self._lock:
            now = self._clock()
            start = now
            if self._global_interval:
                start = max(start, self._next_global)
            if self._site_interval:
                start = max(start, self._next_site.get(site, 0.0))
            if self._global_interval:
                self._next_global = start + self._global_interval
            if self._site_interval:
                self._next_site[site] = start + self._site_interval
            wait = start - now
            if wait > 0:
                self._record(wait)
        if wait > 0:
            self._sleep(wait)
        return wait

    def record_wait(self, seconds):
        """把限速类失败（如 429）的退避时间计入限速等待。"""
        if seconds > 0:
            with self._lock:
                self._record(seconds)

    def _record(self, seconds):
        self.waits += 1
        self.wait_seconds += seconds


def configure(**kwargs):
    """更新全局默认配置，键见 DEFAULTS；返回更新后的配置副本。"""
    unknown = set(kwargs) - set(DEFAULTS)
    if unknown:
        raise TypeError("未知配置项: %s" % ", ".join(sorted(unknown)))
    with _LOCK:
        _CONFIG.update(kwargs)
        return dict(_CONFIG)


def submit(forms, *, sender=None, batch_id=None, **overrides):
    """提交一批表单。

    forms: 可迭代的 dict，可带 "idempotency_key"、"site"/"url" 字段。
    sender: 可选，sender(form, seq) -> dict，返回 {"ok": False, ...} 或抛
        SubmitError 表示失败；默认 sender 直接成功。
    overrides: 覆盖 DEFAULTS 中的配置，另可传 clock/sleep 便于测试。

    返回 {"batch", "results", "summary"}，results 与 forms 逐条对齐。
    """
    cfg, clock, sleep = _runtime(overrides)
    sender = sender or _default_sender
    rows = list(forms)
    batch_id = batch_id or "batch-" + uuid.uuid4().hex[:12]

    now = time.time()
    items = []
    seen = set()
    with _LOCK:
        _purge_registry(now)
        for index, form in enumerate(rows):
            key = _key_of(form)
            item = {
                "index": index,
                "seq": index,
                "idempotency_key": key,
                "site": _site_of(form),
                "status": "pending",
                "ok": False,
                "attempts": 0,
                "reason": None,
                "response": None,
            }
            if key in seen:
                item["status"] = "skipped"
                item["ok"] = True
                item["reason"] = {
                    "category": "duplicate",
                    "message": "同批次内幂等键重复",
                    "seq": index,
                }
            elif key in _REGISTRY:
                item["status"] = "skipped"
                item["ok"] = True
            else:
                seen.add(key)
            items.append(item)
        batch = {
            "id": batch_id,
            "created": now,
            "forms": rows,
            "items": items,
            "rate_limit_waits": 0,
            "rate_limit_wait_seconds": 0.0,
        }
        _BATCHES[batch_id] = batch

    pending = [i for i, it in enumerate(items) if it["status"] == "pending"]
    _run(batch, pending, sender, cfg, clock, sleep)
    return _report(batch, resumed=False)


def resume(batch_id, *, sender=None, **overrides):
    """恢复一个批次：只重提失败项，成功项按幂等键跳过。

    批次不存在（或已过期清理）时返回 {"batch", "resumed": False, "results": []}。
    """
    with _LOCK:
        batch = _BATCHES.get(batch_id)
    if batch is None:
        return {"batch": batch_id, "resumed": False, "results": []}

    cfg, clock, sleep = _runtime(overrides)
    sender = sender or _default_sender
    with _LOCK:
        _purge_registry(time.time())
        for it in batch["items"]:
            if it["status"] == "failed" and it["idempotency_key"] in _REGISTRY:
                # 键已被其它批次成功提交，直接跳过
                it["status"] = "skipped"
                it["ok"] = True
                it["reason"] = None
    failed = [i for i, it in enumerate(batch["items"]) if it["status"] == "failed"]
    _run(batch, failed, sender, cfg, clock, sleep)
    return _report(batch, resumed=True)


def export_detail(batch_id, path=None):
    """导出批次逐条对账明细；path 以 .csv 结尾写 CSV，否则写 JSONL。"""
    with _LOCK:
        batch = _BATCHES.get(batch_id)
    if batch is None:
        return []
    exported_at = time.time()
    rows = []
    for it in batch["items"]:
        reason = it["reason"] or {}
        rows.append({
            "batch": batch_id,
            "index": it["index"],
            "seq": it["seq"],
            "idempotency_key": it["idempotency_key"],
            "site": it["site"],
            "status": it["status"],
            "ok": it["ok"],
            "attempts": it["attempts"],
            "category": reason.get("category"),
            "message": reason.get("message"),
            "form": json.dumps(batch["forms"][it["index"]],
                               ensure_ascii=False, sort_keys=True),
            "exported_at": exported_at,
        })
    if path:
        if str(path).endswith(".csv"):
            fieldnames = list(rows[0].keys()) if rows else [
                "batch", "index", "seq", "idempotency_key", "site", "status",
                "ok", "attempts", "category", "message", "form", "exported_at",
            ]
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def _default_sender(form, seq):
    return {"ok": True, "seq": seq}


def _runtime(overrides):
    overrides = dict(overrides)
    clock = overrides.pop("clock", None) or time.monotonic
    sleep = overrides.pop("sleep", None) or time.sleep
    unknown = set(overrides) - set(DEFAULTS)
    if unknown:
        raise TypeError("未知配置项: %s" % ", ".join(sorted(unknown)))
    with _LOCK:
        cfg = dict(_CONFIG)
    cfg.update(overrides)
    return cfg, clock, sleep


def _key_of(form):
    key = form.get("idempotency_key")
    if key:
        return str(key)
    payload = {k: v for k, v in form.items() if k != "idempotency_key"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


def _site_of(form):
    if form.get("site"):
        return str(form["site"])
    netloc = urlparse(str(form.get("url", ""))).netloc
    return netloc or "default"


def _purge_registry(now):
    expired = [k for k, v in _REGISTRY.items() if v["expires"] <= now]
    for k in expired:
        del _REGISTRY[k]


def _run(batch, indices, sender, cfg, clock, sleep):
    if not indices:
        return
    limiter = RateLimiter(cfg["global_rate"], cfg["site_rate"],
                          clock=clock, sleep=sleep)

    def work(i):
        item = batch["items"][i]
        _deliver(item, batch["forms"][item["index"]], sender, cfg, limiter, sleep)
        if item["status"] == "succeeded":
            with _LOCK:
                _REGISTRY[item["idempotency_key"]] = {
                    "batch": batch["id"],
                    "expires": time.time() + cfg["idempotency_ttl"],
                }

    with ThreadPoolExecutor(max_workers=max(1, cfg["max_workers"])) as pool:
        list(pool.map(work, indices))
    batch["rate_limit_waits"] += limiter.waits
    batch["rate_limit_wait_seconds"] += limiter.wait_seconds


def _deliver(item, form, sender, cfg, limiter, sleep):
    attempts = 0
    while True:
        limiter.acquire(item["site"])
        attempts += 1
        try:
            resp = sender(form, item["seq"])
            if isinstance(resp, dict) and resp.get("ok") is False:
                raise SubmitError(
                    str(resp.get("message", "sender 报告失败")),
                    category=str(resp.get("category", CATEGORY_UNKNOWN)),
                    seq=item["seq"],
                )
            item["status"] = "succeeded"
            item["ok"] = True
            item["response"] = resp
            item["reason"] = None
            break
        except SubmitError as exc:
            category, message = exc.category, str(exc)
        except Exception as exc:  # sender 抛出的未知异常，归入 unknown
            category, message = CATEGORY_UNKNOWN, str(exc)
        item["reason"] = {
            "category": category,
            "message": message,
            "seq": item["seq"],
        }
        if category not in RETRYABLE_CATEGORIES or attempts > cfg["max_retries"]:
            item["status"] = "failed"
            item["ok"] = False
            break
        wait = cfg["backoff_base"] * attempts
        if category == CATEGORY_RATE_LIMITED:
            limiter.record_wait(wait)
        if wait > 0:
            sleep(wait)
    item["attempts"] = attempts


def _report(batch, resumed):
    items = batch["items"]
    summary = {
        "total": len(items),
        "succeeded": sum(1 for it in items if it["status"] == "succeeded"),
        "failed": sum(1 for it in items if it["status"] == "failed"),
        "skipped": sum(1 for it in items if it["status"] == "skipped"),
        "rate_limit_waits": batch["rate_limit_waits"],
        "rate_limit_wait_seconds": round(batch["rate_limit_wait_seconds"], 6),
    }
    batch["summary"] = summary
    report = {
        "batch": batch["id"],
        "results": [_public(it) for it in items],
        "summary": summary,
    }
    if resumed:
        report["resumed"] = True
    return report


def _public(item):
    return {
        "index": item["index"],
        "seq": item["seq"],
        "idempotency_key": item["idempotency_key"],
        "site": item["site"],
        "ok": item["ok"],
        "status": item["status"],
        "attempts": item["attempts"],
        "reason": item["reason"],
        "response": item["response"],
    }

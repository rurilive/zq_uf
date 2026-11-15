"""站点隔离区：隔离带原因、样本和到期时间，到期后低频探测，连续成功才解除。

状态落盘（JSON），多进程共享；手工覆盖写审计。纯标准库。
"""

import json
import os
import tempfile
import time

_DEFAULT_STATE_PATH = os.environ.get("SITEQUARANTINE_STATE") or os.path.join(
    tempfile.gettempdir(), "sitequarantine-state.json")

_CONFIG = {
    "ttl": 0.0,                 # 隔离时长（秒），0 = 立即到期可探测
    "probe_interval": 0.0,      # 探测基础间隔（秒），按隔离次数指数退避
    "backoff_factor": 2.0,      # 退避间隔 = probe_interval * factor ** (隔离次数 - 1)
    "release_after": 1,         # 连续探测成功多少次才解除
    "observation_window": 0.0,  # 解除后观察期（秒），观察期内降权放行
    "observation_weight": 0.5,  # 观察期放行权重
    "sample_limit": 5,          # 每个站点保留的最近样本条数
    "extend_factor": 2.0,       # 探测失败后隔离期延长倍数
    "state_path": _DEFAULT_STATE_PATH,
    "prober": None,             # 站点探测函数 fn(host) -> bool，默认恒可用
}

_REASON_CATEGORIES = (
    ("blocked", ("block", "deny", "forbid", "403", "封")),
    ("timeout", ("timeout", "timed out", "超时")),
    ("dns", ("dns", "resolve", "解析")),
    ("tls", ("tls", "ssl", "cert", "证书")),
    ("http5xx", ("5xx", "500", "502", "503", "504")),
    ("manual", ("manual", "人工")),
)


def configure(**kwargs):
    """更新配置并返回当前配置；无参调用只读。"""
    unknown = set(kwargs) - set(_CONFIG)
    if unknown:
        raise KeyError("unknown config keys: %s" % sorted(unknown))
    _CONFIG.update(kwargs)
    return dict(_CONFIG)


def reset():
    """删除落盘状态（测试/演示用）。"""
    try:
        os.remove(_CONFIG["state_path"])
    except OSError:
        pass
    return True


def classify_reason(reason):
    """把自由文本原因归类，供统计。"""
    text = str(reason).lower()
    for category, keywords in _REASON_CATEGORIES:
        if any(k in text for k in keywords):
            return category
    return "other"


def quarantine(host, reason):
    """隔离站点：记录原因（自动分类）和到期时间，保留既有样本。"""
    state = _load()
    now = time.time()
    entry = state["hosts"].get(host) or _new_entry()
    entry["reason"] = reason
    entry["category"] = classify_reason(reason)
    entry["status"] = "isolated"
    entry["ttl"] = _CONFIG["ttl"]
    entry["until"] = now + _CONFIG["ttl"]
    entry["quarantine_count"] += 1
    entry["consecutive_ok"] = 0
    entry["consecutive_fail"] = 0
    entry["last_probe"] = None
    entry["observation_until"] = None
    entry["override"] = None
    state["hosts"][host] = entry
    categories = state["categories"]
    categories[entry["category"]] = categories.get(entry["category"], 0) + 1
    _save(state)
    return {
        "host": host,
        "reason": reason,
        "category": entry["category"],
        "samples": list(entry["samples"]),
        "until": entry["until"],
        "quarantine_count": entry["quarantine_count"],
    }


def probe(host):
    """探测站点。未到期或退避中不探测；连续成功解除，失败延长隔离。"""
    state = _load()
    now = time.time()
    entry = state["hosts"].get(host)
    if entry is None:
        return {"host": host, "ok": True, "verdict": "open", "samples": []}
    override = entry.get("override")
    if override:
        ok = override["action"] == "release"
        return {"host": host, "ok": ok, "verdict": "open" if ok else "isolated",
                "override": True, "samples": list(entry["samples"])}
    if entry["status"] == "observation":
        return _probe_observation(state, host, entry, now)
    if now < entry["until"]:
        return _isolated(host, entry)
    last = entry["last_probe"]
    if last is not None and now - last < _backoff_interval(entry):
        return _isolated(host, entry, skipped=True)
    ok = _run_prober(host)
    entry["last_probe"] = now
    _add_sample(entry, now, ok)
    if ok:
        entry["consecutive_ok"] += 1
        entry["consecutive_fail"] = 0
        if entry["consecutive_ok"] >= _CONFIG["release_after"]:
            return _release(state, host, entry, now)
    else:
        entry["consecutive_fail"] += 1
        entry["consecutive_ok"] = 0
        base = max(entry.get("ttl", 0.0), _CONFIG["probe_interval"])
        entry["until"] = now + base * (_CONFIG["extend_factor"] ** entry["consecutive_fail"])
    _save(state)
    return _isolated(host, entry, ok=ok)


def override(host, action, operator="unknown", note=""):
    """手工覆盖隔离状态（action: "release" / "isolate"），写审计。"""
    if action not in ("release", "isolate"):
        raise ValueError("action must be 'release' or 'isolate'")
    state = _load()
    now = time.time()
    entry = state["hosts"].get(host)
    if entry is None:
        entry = _new_entry()
        state["hosts"][host] = entry
    if action == "isolate" and not entry["reason"]:
        entry["reason"] = note or "manual"
        entry["category"] = "manual"
        entry["status"] = "isolated"
        entry["until"] = now + _CONFIG["ttl"]
    entry["override"] = {"action": action, "operator": operator,
                         "note": note, "ts": now}
    record = {"ts": now, "host": host, "action": action,
              "operator": operator, "note": note}
    state["audit"].append(record)
    _save(state)
    return dict(record)


def status(host):
    """查看单个站点的隔离状态。"""
    entry = _load()["hosts"].get(host)
    if entry is None:
        return {"host": host, "status": "open"}
    result = {"host": host, "status": entry["status"], "reason": entry["reason"],
              "category": entry["category"], "until": entry["until"],
              "samples": list(entry["samples"]),
              "quarantine_count": entry["quarantine_count"]}
    if entry.get("override"):
        result["override"] = dict(entry["override"])
    return result


def stats():
    """统计：原因分类计数、各状态站点数、审计条数。"""
    state = _load()
    hosts = state["hosts"]
    return {
        "categories": dict(state["categories"]),
        "isolated": sum(1 for e in hosts.values() if e["status"] == "isolated"),
        "observing": sum(1 for e in hosts.values() if e["status"] == "observation"),
        "audits": len(state["audit"]),
    }


def audit():
    """手工覆盖审计记录。"""
    return list(_load()["audit"])


def _new_entry():
    return {"reason": None, "category": "other", "status": "isolated",
            "ttl": 0.0, "until": 0.0, "samples": [], "quarantine_count": 0,
            "consecutive_ok": 0, "consecutive_fail": 0,
            "last_probe": None, "observation_until": None, "override": None}


def _release(state, host, entry, now):
    samples = list(entry["samples"])
    window = _CONFIG["observation_window"]
    if window > 0:
        entry["status"] = "observation"
        entry["observation_until"] = now + window
        _save(state)
        return {"host": host, "ok": True, "verdict": "released",
                "weight": _CONFIG["observation_weight"],
                "observation_until": entry["observation_until"],
                "samples": samples}
    del state["hosts"][host]
    _save(state)
    return {"host": host, "ok": True, "verdict": "open",
            "weight": 1.0, "samples": samples}


def _probe_observation(state, host, entry, now):
    ok = _run_prober(host)
    entry["last_probe"] = now
    _add_sample(entry, now, ok)
    samples = list(entry["samples"])
    if not ok:
        entry["status"] = "isolated"
        entry["quarantine_count"] += 1
        entry["until"] = now + _CONFIG["ttl"]
        entry["consecutive_ok"] = 0
        entry["consecutive_fail"] += 1
        entry["observation_until"] = None
        _save(state)
        return {"host": host, "ok": False, "verdict": "isolated",
                "until": entry["until"], "samples": samples}
    if now >= entry["observation_until"]:
        del state["hosts"][host]
        _save(state)
        return {"host": host, "ok": True, "verdict": "open",
                "weight": 1.0, "samples": samples}
    _save(state)
    return {"host": host, "ok": True, "verdict": "observing",
            "weight": _CONFIG["observation_weight"],
            "observation_until": entry["observation_until"],
            "samples": samples}


def _isolated(host, entry, ok=False, skipped=False):
    due = [entry["until"]]
    if entry["last_probe"] is not None:
        due.append(entry["last_probe"] + _backoff_interval(entry))
    return {"host": host, "ok": ok, "verdict": "isolated",
            "reason": entry["reason"], "until": entry["until"],
            "next_probe_at": max(due), "skipped": skipped,
            "consecutive_ok": entry["consecutive_ok"],
            "samples": list(entry["samples"])}


def _backoff_interval(entry):
    return _CONFIG["probe_interval"] * (
        _CONFIG["backoff_factor"] ** max(0, entry["quarantine_count"] - 1))


def _add_sample(entry, ts, ok):
    samples = entry["samples"]
    samples.append({"ts": ts, "ok": ok})
    del samples[:-max(1, int(_CONFIG["sample_limit"]))]


def _run_prober(host):
    prober = _CONFIG["prober"]
    if prober is None:
        return True
    try:
        return bool(prober(host))
    except Exception:
        return False


def _load():
    try:
        with open(_CONFIG["state_path"], "r", encoding="utf-8") as fh:
            state = json.load(fh)
        state.setdefault("hosts", {})
        state.setdefault("categories", {})
        state.setdefault("audit", [])
        return state
    except (OSError, ValueError):
        return {"hosts": {}, "categories": {}, "audit": []}


def _save(state):
    path = _CONFIG["state_path"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)

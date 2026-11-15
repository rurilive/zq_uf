"""跨进程采集站点预算：状态落盘，文件锁原子扣减，租约到期回收。"""

import contextlib
import fcntl
import json
import os
import time
import uuid

_ENV_DIR = "SITEBUDGET2_DIR"
_DEFAULT_LEASE_SECONDS = 300.0
_config_cache = {}


def _state_dir():
    d = os.environ.get(_ENV_DIR) or os.path.join(os.getcwd(), ".sitebudget2")
    os.makedirs(d, exist_ok=True)
    return d


def _owner():
    return str(os.getpid())


@contextlib.contextmanager
def _locked(d):
    with open(os.path.join(d, "lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load_config(d):
    """读 config.json，按 mtime 探测变更实现热加载。"""
    path = os.path.join(d, "config.json")
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    cached = _config_cache.get(path)
    if cached and cached[0] == sig:
        return cached[1]
    data = {"budgets": {}, "lease_seconds": _DEFAULT_LEASE_SECONDS}
    if sig is not None:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        data["budgets"] = {k: int(v) for k, v in raw.get("budgets", {}).items()}
        data["lease_seconds"] = float(raw.get("lease_seconds", _DEFAULT_LEASE_SECONDS))
    if cached and cached[1] != data:
        _log(d, {"ts": time.time(), "op": "reload", "budgets": data["budgets"]})
    _config_cache[path] = (sig, data)
    return data


def _load_state(d):
    try:
        with open(os.path.join(d, "state.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        return {"sites": {}, "updated": None}


def _save_state(d, state):
    state["updated"] = time.time()
    tmp = os.path.join(d, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, os.path.join(d, "state.json"))


def _log(d, event):
    """所有写操作追加到 events.log，带时间戳，供跨进程对账。"""
    event.setdefault("ts", time.time())
    with open(os.path.join(d, "events.log"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def _reclaim(d, state, now):
    """回收到期租约（含崩溃进程未归还的额度），返回回收事件列表。"""
    reclaimed = []
    for site, st in state["sites"].items():
        for lid in [l for l, v in st["leases"].items() if v["expires"] <= now]:
            v = st["leases"].pop(lid)
            st["used"] = max(0, st["used"] - v["n"])
            ev = {"ts": now, "op": "reclaim", "site": site, "n": v["n"],
                  "lease": lid, "owner": v["owner"]}
            _log(d, ev)
            reclaimed.append(ev)
    return reclaimed


def _wait_seconds(st, budget, n, now):
    """按租约到期顺序估算可等待秒数；n 超过预算上限返回 -1。"""
    if n > budget:
        return -1
    need = st["used"] + n - budget
    freed = 0
    for _, v in sorted(st["leases"].items(), key=lambda kv: kv[1]["expires"]):
        freed += v["n"]
        if freed >= need:
            return max(0.0, round(v["expires"] - now, 3))
    return -1


def take(site, n):
    """原子扣减站点预算；超额不扣，返回 wait 可等待秒数。"""
    n = int(n)
    now = time.time()
    d = _state_dir()
    with _locked(d):
        cfg = _load_config(d)
        state = _load_state(d)
        reclaimed = _reclaim(d, state, now)
        st = state["sites"].setdefault(site, {"used": 0, "leases": {}})
        budget = cfg["budgets"].get(site)
        lease_id = None
        if budget is None or st["used"] + n <= budget:
            lease_id = uuid.uuid4().hex
            st["leases"][lease_id] = {"n": n, "owner": _owner(), "ts": now,
                                      "expires": now + cfg["lease_seconds"]}
            st["used"] += n
            taken, wait = n, 0
            _log(d, {"ts": now, "op": "take", "site": site, "n": n,
                     "lease": lease_id, "owner": _owner()})
        else:
            taken, wait = 0, _wait_seconds(st, budget, n, now)
        _save_state(d, state)
    return {"site": site, "taken": taken, "used": st["used"], "wait": wait,
            "lease": lease_id, "reclaimed": reclaimed}


def give_back(site, n):
    """归还额度，跨进程生效；先消耗本进程租约，再按到期先后。"""
    n = int(n)
    now = time.time()
    d = _state_dir()
    with _locked(d):
        cfg = _load_config(d)
        state = _load_state(d)
        reclaimed = _reclaim(d, state, now)
        st = state["sites"].setdefault(site, {"used": 0, "leases": {}})
        left = min(n, st["used"])
        returned = left
        order = sorted(st["leases"].items(),
                       key=lambda kv: (kv[1]["owner"] != _owner(), kv[1]["expires"]))
        for lid, v in order:
            if left <= 0:
                break
            cut = min(left, v["n"])
            v["n"] -= cut
            st["used"] -= cut
            left -= cut
            if v["n"] == 0:
                del st["leases"][lid]
        _log(d, {"ts": now, "op": "give_back", "site": site, "n": returned,
                 "owner": _owner()})
        _save_state(d, state)
    return {"site": site, "returned": returned, "used": st["used"],
            "reclaimed": reclaimed}


def reclaim():
    """主动回收全部到期租约；任意 take/give_back/stats 也会惰性回收。"""
    now = time.time()
    d = _state_dir()
    with _locked(d):
        state = _load_state(d)
        reclaimed = _reclaim(d, state, now)
        _save_state(d, state)
    return reclaimed


def stats():
    """跨进程对账：占用、按进程明细、events.log 流水三方核对。"""
    now = time.time()
    d = _state_dir()
    with _locked(d):
        cfg = _load_config(d)
        state = _load_state(d)
        _reclaim(d, state, now)
        _save_state(d, state)
        flow = {}
        try:
            with open(os.path.join(d, "events.log"), encoding="utf-8") as fh:
                for line in fh:
                    ev = json.loads(line)
                    if ev.get("op") in ("take", "give_back", "reclaim"):
                        f = flow.setdefault(ev["site"],
                                            {"take": 0, "give_back": 0, "reclaim": 0})
                        f[ev["op"]] += ev.get("n", 0)
        except OSError:
            pass
        sites = {}
        balanced = True
        for site, st in state["sites"].items():
            owners = {}
            for v in st["leases"].values():
                owners[v["owner"]] = owners.get(v["owner"], 0) + v["n"]
            budget = cfg["budgets"].get(site)
            f = flow.get(site, {"take": 0, "give_back": 0, "reclaim": 0})
            ok = f["take"] - f["give_back"] - f["reclaim"] == st["used"]
            balanced = balanced and ok
            sites[site] = {
                "used": st["used"], "budget": budget,
                "available": None if budget is None else max(0, budget - st["used"]),
                "leases": len(st["leases"]), "owners": owners,
                "journal": f, "balanced": ok,
            }
        return {"sites": sites, "balanced": balanced, "ts": now}

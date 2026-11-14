"""代理流量配额。

两种后端，通过 :func:`configure` 切换：

* 内存模式（默认）：保持历史的全局计数语义，``_spent``/``LIMIT`` 仍是
  进程内全局整数，单进程老代码和老测试无需改动。
* 文件模式（``state_dir`` 指向一个目录后启用）：额度按 (站点, 出口) 计量，
  每个站点独立配额，状态落盘并通过 ``fcntl.flock`` 串行化，多进程合计
  一致；超额时返回可等待秒数并切换出口，全部出口用尽才抛 ``RuntimeError``；
  按天/按月输出消耗、剩余和切换记录。
"""

import datetime as _dt
import errno
import fcntl
import json
import os
import tempfile

# 内存模式（默认后端）使用的全局计数，保持历史语义。
_spent = 0
LIMIT = 1000

# 文件模式的运行配置：None 表示仍走内存模式。
_config = None

# 时间 seam，测试里可替换以跨天/跨月造账。
_now = _dt.datetime.now


def configure(state_dir=None, default_limit=LIMIT, site_limits=None,
              proxies=None):
    """切换计量后端。

    传入 ``state_dir`` 即启用文件模式，状态文件为该目录下的
    ``proxybudget_state.json``。``site_limits`` 形如 ``{"shop": 500}``，
    未列出的站点使用 ``default_limit``。``proxies`` 是各站点可切换的
    出口池，形如 ``{"shop": ["p1", "p2"]}``；consume 中出现的出口也会
    自动入池。不带参数调用则恢复内存模式。
    """
    global _config
    if state_dir is None:
        _config = None
        return
    _config = {
        "state_dir": state_dir,
        "default_limit": int(default_limit),
        "site_limits": {k: int(v) for k, v in (site_limits or {}).items()},
        "proxies": {k: list(v) for k, v in (proxies or {}).items()},
    }
    os.makedirs(state_dir, exist_ok=True)


def reset():
    """清空文件模式的全部状态（主要给测试用）。"""
    if _config is None:
        return
    try:
        os.unlink(_state_path())
    except OSError as exc:
        if exc.errno != errno.ENOENT:
            raise


# ----------------------------------------------------------------------
# 文件后端内部实现
# ----------------------------------------------------------------------

def _state_path():
    return os.path.join(_config["state_dir"], "proxybudget_state.json")


def _lock_path():
    # 锁文件与状态文件分离且永不被替换：flock 绑定 inode，若直接锁状态
    # 文件，os.replace 原子换文件后其他进程手里的 fd 会指向旧 inode，
    # 锁空间随之分裂，多进程互斥就失效了。
    return os.path.join(_config["state_dir"], "proxybudget.lock")


def _today():
    return _now().date().isoformat()


def _new_state():
    return {
        "version": 1,
        "day": None,           # spent 计数所属的日期(YYYY-MM-DD)
        "spent": {},            # site -> proxy -> 当日已消耗
        "daily": {},            # 日期(YYYY-MM-DD) -> site -> proxy -> 消耗
        "switches": [],         # 切换/超额事件
        "active": {},           # site -> 当前出口
        "pools": {},            # site -> 出口池
    }


class _locked_state:
    """持有独占文件锁的状态上下文。

    进入时在独立锁文件上加 ``flock`` 并载入状态 JSON，退出时原子替换
    写回状态文件再释放锁。锁是进程级的，因此整个读-改-写在多进程间
    是原子的；锁文件本身从不替换，避免 inode 漂移导致锁分裂。
    """

    def __enter__(self):
        state_dir = _config["state_dir"]
        os.makedirs(state_dir, exist_ok=True)
        self._lock_fh = open(_lock_path(), "a+", encoding="utf-8")
        fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX)
        path = _state_path()
        try:
            with open(path, encoding="utf-8") as fh:
                self.state = json.loads(fh.read())
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
            self.state = _new_state()
        return self.state

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._save()
        finally:
            self._lock_fh.close()
        return False

    def _save(self):
        directory = _config["state_dir"]
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp",
                                   dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, _state_path())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _limit_for(site):
    return _config["site_limits"].get(site, _config["default_limit"])


def _pool_for(state, site, proxy):
    """返回站点的出口池，并把当前 proxy 纳入池中。"""
    pool = state.setdefault("pools", {}).setdefault(site, [])
    configured = _config["proxies"].get(site, [])
    for name in configured + [proxy]:
        if name not in pool:
            pool.append(name)
    return pool


def _rollover(state, today):
    """跨天时清零当日计数。

    每笔扣减在 :func:`_deduct` 中已实时写入 daily 账本，所以这里只需
    重置计数器并记下新日期，避免把昨天的用量错误归到今天。
    """
    if state.get("day") != today:
        state["spent"] = {}
        state["day"] = today


def _spent_on(state, site, proxy, today):
    _rollover(state, today)
    return state["spent"].get(site, {}).get(proxy, 0)


def _deduct(state, site, proxy, amount, today):
    """在已持锁的状态上记账并返回该出口当日累计消耗。"""
    _rollover(state, today)
    state["active"][site] = proxy
    by_proxy = state["spent"].setdefault(site, {})
    by_proxy[proxy] = by_proxy.get(proxy, 0) + amount
    day_bucket = state["daily"].setdefault(today, {})
    site_bucket = day_bucket.setdefault(site, {})
    site_bucket[proxy] = site_bucket.get(proxy, 0) + amount
    return by_proxy[proxy]


def _seconds_until_midnight(now):
    nxt = _dt.datetime.combine(now.date() + _dt.timedelta(days=1),
                               _dt.time.min)
    return max(1, int(round((nxt - now).total_seconds())))


def _record_switch(state, site, old, new, amount, retry_after, limit):
    event = {
        "time": _now().isoformat(timespec="seconds"),
        "site": site,
        "from_proxy": old,
        "to_proxy": new,
        "requested": amount,
        "retry_after": retry_after,
        "limit": limit,
        "reason": "quota_exceeded",
    }
    state["switches"].append(event)
    return event


def _consume_file(site, proxy, amount):
    if amount < 0:
        raise ValueError("amount must be non-negative")
    with _locked_state() as state:
        today = _today()
        limit = _limit_for(site)
        pool = _pool_for(state, site, proxy)
        used = _spent_on(state, site, proxy, today)
        if used + amount <= limit:
            total = _deduct(state, site, proxy, amount, today)
            return {"site": site, "proxy": proxy, "spent": total,
                    "remaining": limit - total, "switched": False}

        # 当前出口超额：先记录该出口要等到次日零点才恢复。
        now = _now()
        retry_after = _seconds_until_midnight(now)
        candidates = [p for p in pool if p != proxy]
        for candidate in candidates:
            candidate_used = state["spent"].get(site, {}).get(candidate, 0)
            if candidate_used + amount <= limit:
                event = _record_switch(state, site, proxy, candidate,
                                       amount, retry_after, limit)
                total = _deduct(state, site, candidate, amount, today)
                return {"site": site, "proxy": candidate, "spent": total,
                        "remaining": limit - total, "switched": True,
                        "retry_after": retry_after,
                        "retry_after_for": proxy, "event": event}

        # 池内没有任何出口能承接：任务无法继续，等待时间随异常带出。
        event = _record_switch(state, site, proxy, None, amount,
                               retry_after, limit)
        err = RuntimeError(
            "site %r quota exceeded on all %d proxies; retry after %ds"
            % (site, len(pool), retry_after))
        err.retry_after = retry_after
        err.site = site
        err.event = event
        raise err


def _remaining_file(site):
    with _locked_state() as state:
        today = _today()
        limit = _limit_for(site)
        # 触发跨天检查：若已跨天则清零当日计数并落盘，避免读到昨天的用量。
        _rollover(state, today)
        active = state["active"].get(site)
        if active is None:
            return limit
        used = state["spent"].get(site, {}).get(active, 0)
        return limit - used


def _load_state_locked():
    """加锁读取一份状态快照（对账用，读操作不修改文件）。"""
    if not os.path.exists(_state_path()):
        return _new_state()
    with open(_lock_path(), "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_SH)
        with open(_state_path(), encoding="utf-8") as fh:
            raw = fh.read()
        return json.loads(raw) if raw.strip() else _new_state()


def _site_totals(state, day, site):
    entries = state["daily"].get(day, {}).get(site, {})
    spent = sum(entries.values())
    limit = _limit_for(site)
    return {
        "spent": spent,
        "remaining": max(0, limit - spent),
        "limit": limit,
        "by_proxy": dict(entries),
    }


def daily_report(day=None):
    """按天对账：每个站点的消耗、剩余、分出口明细和当日切换记录。"""
    if _config is None:
        return {"date": day or _today(), "sites": {}, "switches": []}
    state = _load_state_locked()
    day = day or _today()
    sites = {site: _site_totals(state, day, site)
             for site in state["daily"].get(day, {})}
    switches = [e for e in state["switches"]
                if e["time"].startswith(day)]
    return {"date": day, "sites": sites, "switches": switches}


def monthly_report(month=None):
    """按月对账：聚合该月每日账本，输出消耗、剩余和整月切换记录。

    ``month`` 形如 ``"2026-11"``，默认当月。
    """
    if _config is None:
        return {"month": month or _today()[:7], "sites": {}, "switches": []}
    state = _load_state_locked()
    month = month or _today()[:7]
    agg = {}
    for day, sites in state["daily"].items():
        if not day.startswith(month):
            continue
        for site, by_proxy in sites.items():
            bucket = agg.setdefault(site, {})
            for proxy, amount in by_proxy.items():
                bucket[proxy] = bucket.get(proxy, 0) + amount
    sites = {}
    for site, by_proxy in agg.items():
        limit = _limit_for(site)
        spent = sum(by_proxy.values())
        sites[site] = {
            "spent": spent,
            "remaining": max(0, limit - spent),
            "limit": limit,
            "by_proxy": dict(by_proxy),
        }
    switches = [e for e in state["switches"]
                if e["time"].startswith(month)]
    return {"month": month, "sites": sites, "switches": switches}


def save_reports(state_dir=None):
    """把当天日报和当月月报落盘，返回两个文件路径。"""
    base = state_dir or (_config or {}).get("state_dir")
    if base is None:
        raise RuntimeError("file backend is not enabled; call configure() first")
    day = daily_report()
    month = monthly_report()
    day_path = os.path.join(base, "report_daily_%s.json" % day["date"])
    month_path = os.path.join(base, "report_monthly_%s.json" % month["month"])
    for path, payload in ((day_path, day), (month_path, month)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    return day_path, month_path


# ----------------------------------------------------------------------
# 公共接口：签名保持 consume(site, proxy, amount) / remaining(site)
# ----------------------------------------------------------------------

def consume(site, proxy, amount):
    """扣减流量配额。

    内存模式：沿用历史全局计数，超额抛 ``RuntimeError``。
    文件模式：按 (站点, 出口) 独立计量。正常扣减返回结果 dict；当前出口
    超额时自动切到池内其他出口，返回中带 ``retry_after``（原出口到次日
    零点的可等待秒数）和切换记录；所有出口都超额时抛带 ``retry_after``
    属性的 ``RuntimeError``，不再静默中断正在跑的任务。
    """
    if _config is None:
        global _spent
        if _spent + amount > LIMIT:
            raise RuntimeError("global quota exceeded")
        _spent += amount
        return _spent
    return _consume_file(site, proxy, amount)


def remaining(site=None):
    """返回站点当前出口的剩余额度；内存模式下返回全局剩余。"""
    if _config is None:
        return LIMIT - _spent
    return _remaining_file(site)

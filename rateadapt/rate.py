"""采集限速自适应模块。

按响应（成功率 / 429 占比）沿档位调整速率：连续成功慢涨，被限流快降。
限速与并发共用一个按组分桶的令牌预算，突发无法绕过。
调整有冷却和上下限，可回滚上一档，调整记录可按站点回放。
"""

import time

# 速率档位（QPS），调整以档为单位，便于回滚到上一档
LEVELS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
DEFAULT_LEVEL = 2  # 1.0 QPS

UP_AFTER = 10        # 连续成功多少次才上调一档（慢涨）
DOWN_STEPS_429 = 2   # 触发 429 降速时一次下掉的档数（快降）
DOWN_STEPS_ERROR = 1  # 成功率过低时一次下掉的档数
WINDOW = 10          # 统计最近多少条反馈
MIN_SAMPLES = 5      # 样本不足此数不调整
RATIO_429 = 0.3      # 最近窗口内 429 占比达到即降速
MIN_SUCCESS = 0.5    # 最近窗口内成功率低于即降速
COOLDOWN = 30.0      # 两次调整之间的冷却秒数
BUDGET_WINDOW = 4.0  # 预算容量 = 组内速率之和 × 窗口秒数

_now = time.monotonic  # 测试/演示可替换为假时钟

_sites = {}   # site -> 状态
_groups = {}  # group -> {"tokens": float, "updated": float}


def _site(site):
    st = _sites.get(site)
    if st is None:
        st = _sites[site] = {
            "level": DEFAULT_LEVEL,
            "group": site,
            "records": [],       # 限速记录：响应码 + 耗时
            "adjustments": [],   # 调整记录，可回放
            "prev": [],          # 历史档位栈，用于回滚
            "streak": 0,         # 连续成功计数
            "last_adjust": None,
        }
        _groups[site] = {"tokens": LEVELS[DEFAULT_LEVEL] * BUDGET_WINDOW,
                         "updated": _now()}
    return st


def _group_rate(group):
    return sum(LEVELS[st["level"]] for st in _sites.values()
               if st["group"] == group)


def _bucket(group):
    """取组预算桶并按经过时间补充；限速与并发共用这一个桶。"""
    now = _now()
    b = _groups.setdefault(group, {"tokens": 0.0, "updated": now})
    rate = _group_rate(group)
    cap = rate * BUDGET_WINDOW
    b["tokens"] = min(cap, b["tokens"] + rate * max(0.0, now - b["updated"]))
    b["updated"] = now
    return b, cap


def _adjust(st, delta, reason, now):
    old = st["level"]
    new = min(max(old + delta, 0), len(LEVELS) - 1)  # 上下限
    if new == old:
        return False, "at_cap" if delta > 0 else "at_floor"
    st["prev"].append(old)
    st["level"] = new
    st["last_adjust"] = now
    st["adjustments"].append({"ts": now, "from": LEVELS[old],
                              "to": LEVELS[new], "reason": reason})
    return True, reason


def acquire(site):
    """取一份预算；预算耗尽（限速/并发共用）则拒绝。"""
    st = _site(site)
    bucket, cap = _bucket(st["group"])
    ok = bucket["tokens"] >= 1.0
    if ok:
        bucket["tokens"] -= 1.0
    return {
        "site": site,
        "ok": ok,
        "rate": LEVELS[st["level"]],
        "reason": "ok" if ok else "budget_exhausted",
        "budget": round(bucket["tokens"], 4),
        "budget_cap": cap,
        "group": st["group"],
    }


def feedback(site, status, rtt):
    """记录响应码与耗时，按成功率 / 429 占比调速（带冷却）。"""
    st = _site(site)
    now = _now()
    st["records"].append({"ts": now, "status": status, "rtt": rtt})
    st["streak"] = st["streak"] + 1 if 200 <= status < 400 else 0

    adjusted, reason = False, None
    recent = st["records"][-WINDOW:]
    if len(recent) < MIN_SAMPLES:
        reason = "insufficient_samples"
    elif st["last_adjust"] is not None and now - st["last_adjust"] < COOLDOWN:
        reason = "cooldown"
    else:
        ratio_429 = sum(1 for r in recent if r["status"] == 429) / len(recent)
        succ = sum(1 for r in recent if 200 <= r["status"] < 400) / len(recent)
        if ratio_429 >= RATIO_429:
            adjusted, reason = _adjust(st, -DOWN_STEPS_429, "throttled_429", now)
        elif succ < MIN_SUCCESS:
            adjusted, reason = _adjust(st, -DOWN_STEPS_ERROR,
                                       "low_success_rate", now)
        elif st["streak"] >= UP_AFTER:
            adjusted, reason = _adjust(st, 1, "success_streak", now)
            if adjusted:
                st["streak"] = 0
        else:
            reason = "within_tolerance"

    bucket, cap = _bucket(st["group"])
    return {
        "site": site,
        "rate": LEVELS[st["level"]],
        "adjusted": adjusted,
        "reason": reason,
        "budget": round(bucket["tokens"], 4),
        "budget_cap": cap,
        "group": st["group"],
    }


def rollback(site):
    """回滚到上一档速率。"""
    st = _site(site)
    if not st["prev"]:
        return {"site": site, "rate": LEVELS[st["level"]],
                "adjusted": False, "reason": "no_previous_level"}
    now = _now()
    old = st["level"]
    st["level"] = st["prev"].pop()
    st["last_adjust"] = now
    st["adjustments"].append({"ts": now, "from": LEVELS[old],
                              "to": LEVELS[st["level"]], "reason": "rollback"})
    return {"site": site, "rate": LEVELS[st["level"]],
            "adjusted": True, "reason": "rollback"}


def replay(site):
    """按站点回放调整记录（时间、原档、新档、原因）。"""
    return list(_site(site)["adjustments"])


def records(site):
    """限速记录：每条带响应码和耗时。"""
    return list(_site(site)["records"])


def join_group(site, group):
    """把站点加入分组，同组站点共享一份预算。"""
    st = _site(site)
    st["group"] = group
    if group not in _groups:
        _groups[group] = {"tokens": _group_rate(group) * BUDGET_WINDOW,
                          "updated": _now()}
    return {"site": site, "group": group}

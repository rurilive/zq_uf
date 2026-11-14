"""采集预热：阶梯提升并发，按成功率反馈，连续失败回退并冷却，状态可序列化。"""

import json
import time

STAGES = (1, 2, 4, 8)        # 阶梯档位，顶档保持 8
PROMOTE_RATE = 0.9           # 观察窗口成功率达到则升一档
REGRESS_RATE = 0.5           # 观察窗口成功率低于则回退一档
MIN_WINDOW = 4               # 观察窗口最小样本数，不足不评估
CONSEC_FAIL_LIMIT = 3        # 连续失败达到即回退
COOLDOWN_SECONDS = 30.0      # 回退后的冷却时长（秒），冷却内不升档
DEFAULT_CONCURRENCY = STAGES[-1]

_sites = {}


def _new_state():
    return {
        "stage": 0,
        "consec_failures": 0,
        "cooldown_until": 0.0,
        "history": [],   # 每个观察窗口一条：阶段、并发、成功率
        "backoffs": [],  # 回退记录，含上一档快照
    }


def _stats(window):
    """window 为 (成功数, 失败数) 或 bool 序列，返回 (成功数, 总数, 末尾连续失败数)。"""
    if isinstance(window, tuple):
        ok, fail = window
        return ok, ok + fail, fail
    results = list(window)
    ok = sum(1 for r in results if r)
    trailing = 0
    for r in reversed(results):
        if r:
            break
        trailing += 1
    return ok, len(results), trailing


def _regress(state, rate, ts):
    snapshot = {
        "stage": state["stage"],
        "concurrency": STAGES[state["stage"]],
        "success_rate": rate,
    }
    state["stage"] = max(0, state["stage"] - 1)
    state["consec_failures"] = 0
    state["cooldown_until"] = ts + COOLDOWN_SECONDS
    state["backoffs"].append({
        "from": snapshot,
        "to_stage": state["stage"],
        "to_concurrency": STAGES[state["stage"]],
        "cooldown_until": state["cooldown_until"],
    })


def _report(site, state, rate, in_cooldown):
    return {
        "site": site,
        "stage": state["stage"],
        "concurrency": STAGES[state["stage"]],
        "success_rate": rate,
        "in_cooldown": in_cooldown,
        "backoffs": [dict(b) for b in state["backoffs"]],
    }


def warmup(site, window=None, now=None):
    """推进站点预热。

    window 为 None 表示无反馈调用，直接完成到顶档（兼容旧行为）；
    否则 window 是一个观察窗口：(成功数, 失败数) 或 bool 序列，
    按窗口成功率升档/回退。now 可注入时间（秒），便于测试与演示。
    """
    state = _sites.setdefault(site, _new_state())
    if window is None:
        state["stage"] = len(STAGES) - 1
        return _report(site, state, None, False)

    ts = time.monotonic() if now is None else now
    ok, total, trailing = _stats(window)
    rate = ok / total if total else 1.0
    in_cooldown = ts < state["cooldown_until"]

    if total >= MIN_WINDOW and not in_cooldown:
        # 末尾连续失败构成当前连败；全失败的窗口与上一窗口连败累加
        if trailing == total:
            state["consec_failures"] += trailing
        else:
            state["consec_failures"] = trailing
        if rate >= PROMOTE_RATE:
            state["consec_failures"] = 0
            if state["stage"] < len(STAGES) - 1:
                state["stage"] += 1
        elif rate < REGRESS_RATE or state["consec_failures"] >= CONSEC_FAIL_LIMIT:
            _regress(state, rate, ts)
    state["history"].append({
        "stage": state["stage"],
        "concurrency": STAGES[state["stage"]],
        "success_rate": rate,
    })
    return _report(site, state, rate, in_cooldown)


def current(site):
    """站点当前档位的并发数；未预热的站点返回顶档（兼容旧行为）。"""
    state = _sites.get(site)
    return STAGES[state["stage"]] if state else DEFAULT_CONCURRENCY


def dump_state():
    """导出全部站点的预热状态，返回值可 JSON 序列化。"""
    return {
        "sites": {
            name: {
                **s,
                "history": list(s["history"]),
                "backoffs": list(s["backoffs"]),
            }
            for name, s in _sites.items()
        }
    }


def dumps():
    """dump_state 的 JSON 字符串形式。"""
    return json.dumps(dump_state())


def load_state(state):
    """从快照恢复，重启后从各站点当前档继续。"""
    _sites.clear()
    for name, s in state.get("sites", {}).items():
        restored = _new_state()
        restored.update(s)
        _sites[name] = restored


def loads(text):
    """从 JSON 字符串恢复状态。"""
    load_state(json.loads(text))


def reset():
    """清空全部站点状态。"""
    _sites.clear()

"""动态权重站点调度。

按滑动窗口内的历史成功率与更新频率计算站点权重，连续失败自动降权、
恢复后自动回升；低权站点通过权重下限（软保底）和等待上限（硬保底）
双重机制避免饿死。仅使用 Python 标准库。
"""

from collections import deque

SITES = ["shop", "news", "blog"]

# ---- 调度参数（README 中有完整公式与口径说明）----
BASE_WEIGHT = 10
MIN_WEIGHT = 2
WINDOW_SIZE = 20
MIN_SAMPLES = 3
FAIL_THRESHOLD = 3
FAIL_PENALTY = 0.5
RECOVER_THRESHOLD = 2
SUCCESS_WEIGHT = 0.7
FREQUENCY_WEIGHT = 0.3
STARVATION_LIMIT = 2 * len(SITES)


class SiteWeightError(Exception):
    """调度器基础异常，所有错误路径显式抛出，不静默跳过。"""


class UnknownSiteError(SiteWeightError):
    """feedback 收到未在 SITES 中配置的站点。"""


_state = None


def _fresh_state():
    return {
        "windows": {site: deque(maxlen=WINDOW_SIZE) for site in SITES},
        "fail_streak": {site: 0 for site in SITES},
        "success_streak": {site: 0 for site in SITES},
        "penalized": {site: False for site in SITES},
        "weight": {site: BASE_WEIGHT for site in SITES},
        "current": [0 for _ in SITES],
        "wait_counter": {site: 0 for site in SITES},
        "waits": {site: [] for site in SITES},
        "picks": {site: 0 for site in SITES},
        "events": [],
        "transition": (None, None),
        "pick_counter": 0,
        "starvation_picks": 0,
        "feedback_total": 0,
    }


def reset():
    """清空全部历史状态，回到等权冷启动。"""
    global _state
    _state = _fresh_state()


reset()


def _check_site(site):
    if not isinstance(site, str) or site not in SITES:
        raise UnknownSiteError(
            f"unknown site {site!r}; configured sites: {SITES}"
        )


def _update_penalty(site):
    """连续失败达到阈值进入降权，降权后连续成功达到阈值解除。"""
    st = _state
    before = st["penalized"][site]
    if st["fail_streak"][site] >= FAIL_THRESHOLD:
        st["penalized"][site] = True
    elif before and st["success_streak"][site] >= RECOVER_THRESHOLD:
        st["penalized"][site] = False
    after = st["penalized"][site]
    if before and not after:
        st["transition"] = (site, "recovered")
    elif not before and after:
        st["transition"] = (site, "demoted")


def _change_reason(site):
    transition_site, kind = _state["transition"]
    if transition_site == site and kind is not None:
        if kind == "demoted":
            return "consecutive_failures"
        return "recovered"
    return "success_rate"


def _recompute_weights():
    """按当前窗口重算全部站点权重，变化写入事件日志。"""
    st = _state
    lengths = {site: len(st["windows"][site]) for site in SITES}
    max_len = max(lengths.values()) if SITES else 0
    for site in SITES:
        window = st["windows"][site]
        if len(window) < MIN_SAMPLES:
            new_weight = BASE_WEIGHT
        else:
            success_rate = sum(window) / len(window)
            freq_ratio = lengths[site] / max_len if max_len else 1.0
            score = (
                SUCCESS_WEIGHT * success_rate
                + FREQUENCY_WEIGHT * freq_ratio
            )
            new_weight = round(BASE_WEIGHT * score)
        if st["penalized"][site]:
            new_weight = round(new_weight * FAIL_PENALTY)
        new_weight = min(BASE_WEIGHT, max(MIN_WEIGHT, new_weight))
        old_weight = st["weight"][site]
        if new_weight != old_weight:
            st["events"].append(
                {
                    "site": site,
                    "old": old_weight,
                    "new": new_weight,
                    "reason": _change_reason(site),
                    "pick": st["pick_counter"],
                }
            )
            st["weight"][site] = new_weight


def _apply_swrr(forced_index):
    """平滑加权轮转一步；权重相等时退化为固定顺序轮转。"""
    st = _state
    total = sum(st["weight"][site] for site in SITES)
    for index, site in enumerate(SITES):
        st["current"][index] += st["weight"][site]
    if forced_index is None:
        forced_index = max(range(len(SITES)), key=lambda i: st["current"][i])
    st["current"][forced_index] -= total
    return forced_index


def _pick_index():
    st = _state
    longest = max(st["wait_counter"].values())
    if longest >= STARVATION_LIMIT:
        st["starvation_picks"] += 1
        for index, site in enumerate(SITES):
            if st["wait_counter"][site] == longest:
                return _apply_swrr(index)
    return _apply_swrr(None)


def next_site():
    """返回下一个应调度的站点，更新等待统计。"""
    if not SITES:
        raise SiteWeightError("no sites configured; cannot schedule next site")
    st = _state
    st["pick_counter"] += 1
    for site in SITES:
        st["wait_counter"][site] += 1
    index = _pick_index()
    site = SITES[index]
    wait = st["wait_counter"][site]
    st["waits"][site].append(wait)
    st["wait_counter"][site] = 0
    st["picks"][site] += 1
    return site


next = next_site


def feedback(site, result):
    """回传一次采集结果，立即重算权重。result 必须是 bool。"""
    if not isinstance(result, bool):
        raise TypeError(
            f"result must be bool, got {type(result).__name__}: {result!r}"
        )
    _check_site(site)
    st = _state
    st["feedback_total"] += 1
    st["transition"] = (None, None)
    window = st["windows"][site]
    window.append(result)
    if result:
        st["success_streak"][site] += 1
        st["fail_streak"][site] = 0
    else:
        st["fail_streak"][site] += 1
        st["success_streak"][site] = 0
    _update_penalty(site)
    _recompute_weights()
    return st["feedback_total"]


def weights():
    """返回各站点当前有效权重的副本。"""
    return dict(_state["weight"])


def weight_changes():
    """返回权重变化事件列表副本，含 site/old/new/reason/pick。"""
    return [dict(event) for event in _state["events"]]


def stats():
    """返回选中次数、等待时间、窗口样本等调度统计。"""
    st = _state
    return {
        "total_picks": st["pick_counter"],
        "starvation_picks": st["starvation_picks"],
        "sites": {
            site: {
                "weight": st["weight"][site],
                "picks": st["picks"][site],
                "current_wait": st["wait_counter"][site],
                "max_wait": max(st["waits"][site], default=0),
                "avg_wait": (
                    sum(st["waits"][site]) / len(st["waits"][site])
                    if st["waits"][site]
                    else 0.0
                ),
                "feedback": len(st["windows"][site]),
                "success_rate": (
                    sum(st["windows"][site]) / len(st["windows"][site])
                    if st["windows"][site]
                    else None
                ),
                "penalized": st["penalized"][site],
            }
            for site in SITES
        },
    }

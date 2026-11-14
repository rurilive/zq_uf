"""按站点自适应限速。

模型：
- 每个站点一个独立令牌桶，令牌按当前速率 R（tokens/s）补充，桶容量等于并发上限 C。
- 调度使用单调递增的虚拟时钟 vclock（即旧接口里的 _last[0]），acquire 返回的
  "at" 是虚拟时刻，"wait" 是换算到墙上时钟的建议等待秒数。
- 全部状态落盘 JSON，每次操作持 fcntl 排他文件锁，多进程共享同一份每站状态，
  合计发放的许可不超过该站速率。
- release 根据状态码、成功率窗口和 RTT 的 EMA 调整 R 与 C：连续 429/5xx 立即
  降速，稳定后缓慢恢复。每站状态独立，互不拖累。
"""

import fcntl
import json
import os
import threading
import time

# 兼容旧接口：默认速率 1.0/s 对应的固定间隔
INTERVAL = 1.0

# ---- 自适应参数（公式见 README） ----
DEFAULT_RATE = 1.0          # 默认令牌补充速率 tokens/s
MIN_RATE = 0.1              # 速率下限
MAX_RATE = 10.0             # 速率上限
DEFAULT_CONCURRENCY = 4     # 默认并发上限（同时也是令牌桶容量）
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 16

FAIL_THRESHOLD = 2          # 连续 429/5xx 达到该次数即降速
FAIL_FACTOR = 0.5           # 故障降速乘法因子
RTT_ALPHA = 0.3             # RTT EMA 平滑系数
RTT_HIGH = 2.0              # EMA 超过基线的倍数，判定 RTT 高压
RTT_RECOVER = 1.5           # EMA 回落到基线该倍数以内才允许恢复
RTT_FACTOR = 0.9            # RTT 高压降速乘法因子
RTT_COOLDOWN = 3            # 两次自适应调整之间的最小 release 次数
RECOVER_STREAK = 5          # 连续成功多少次后缓慢恢复
RECOVER_FACTOR = 1.1        # 恢复提速乘法因子
WINDOW_MIN = 10             # 成功率窗口至少积累这么多样本才参与判断
WINDOW_LIMIT = 20           # 成功率滑动窗口大小
SUCCESS_RATE_FLOOR = 0.5    # 成功率低于该值触发降速
MAX_REASONS = 5             # 每站保留的最近调整原因条数

_thread_lock = threading.RLock()


class _ResetList(list):
    """兼容旧测试：对 _last[0] 赋值即重置全部限速状态（含落盘文件）。"""

    def __setitem__(self, index, value):
        super().__setitem__(index, value)
        if index == 0:
            reset()


# 全局虚拟时钟；正常流程只通过 list.__setitem__ 写入，避免触发重置钩子
_last = _ResetList([0.0])


def _state_path():
    return os.environ.get(
        "HOSTSHIELD_STATE_FILE",
        os.path.join(os.path.expanduser("~"), ".hostshield", "state.json"),
    )


def _fresh_state():
    return {
        "version": 1,
        "vclock": 0.0,
        "wall_origin": time.monotonic(),
        "sites": {},
    }


def _new_site():
    return {
        "rate": DEFAULT_RATE,
        "concurrency": DEFAULT_CONCURRENCY,
        "tokens": float(DEFAULT_CONCURRENCY),
        "updated_at": 0.0,
        "next_at": 0.0,
        "in_flight": 0,
        "rtt_ema": None,
        "rtt_base": None,
        "ok_streak": 0,
        "fail_streak": 0,
        "cooldown": 0,
        "outcomes": [],
        "successes": 0,
        "failures": 0,
        "last_reason": "初始化：速率 %.2f/s，并发上限 %d"
        % (DEFAULT_RATE, DEFAULT_CONCURRENCY),
        "reasons": [],
    }


# ---------------- 参数校验 ----------------

def _validate_site(site):
    if not isinstance(site, str) or not site:
        raise ValueError("site 必须是非空字符串，收到 %r" % (site,))


def _validate_rtt(rtt):
    if isinstance(rtt, bool) or not isinstance(rtt, (int, float)) or rtt < 0:
        raise ValueError("rtt 必须是非负数值（秒），收到 %r" % (rtt,))


def _validate_status(status):
    if isinstance(status, bool) or not isinstance(status, int) \
            or not (100 <= status <= 599):
        raise ValueError("status 必须是 100..599 之间的整数，收到 %r" % (status,))


# ---------------- 落盘与文件锁 ----------------

class _Store:
    """持排他文件锁打开共享状态，退出时原子写回。"""

    def __enter__(self):
        _thread_lock.acquire()
        self._path = _state_path()
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = open(self._path + ".lock", "a+")
        fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX)
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self.state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.state = _fresh_state()
        # 同步虚拟时钟镜像，走 list 基类方法，不触发重置钩子
        list.__setitem__(_last, 0, self.state["vclock"])
        return self.state

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self.state["vclock"] = _last[0]
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        self._lock.close()
        _thread_lock.release()


def reset():
    """清空全部站点状态与虚拟时钟（主要供测试使用）。"""
    with _thread_lock:
        path = _state_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path + ".lock", "a+") as lock_fp:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            state = _fresh_state()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        list.__setitem__(_last, 0, 0.0)


# ---------------- 自适应调整 ----------------

def _record(site_state, reason):
    site_state["last_reason"] = reason
    site_state["reasons"].append(reason)
    if len(site_state["reasons"]) > MAX_REASONS:
        del site_state["reasons"][:-MAX_REASONS]


def _apply_capacity(site_state):
    # 桶容量随并发上限变化，已有令牌不能超过新容量
    site_state["tokens"] = min(site_state["tokens"], site_state["concurrency"])


def _slow_down(site_state, why):
    old_rate = site_state["rate"]
    old_conc = site_state["concurrency"]
    site_state["rate"] = max(MIN_RATE, round(old_rate * FAIL_FACTOR, 6))
    site_state["concurrency"] = max(MIN_CONCURRENCY, old_conc - 1)
    site_state["cooldown"] = RTT_COOLDOWN
    _apply_capacity(site_state)
    _record(
        site_state,
        "%s：速率 %.2f→%.2f/s，并发上限 %d→%d"
        % (why, old_rate, site_state["rate"], old_conc,
           site_state["concurrency"]),
    )


def _update_rtt(site_state, rtt):
    rtt = float(rtt)
    if site_state["rtt_ema"] is None:
        site_state["rtt_ema"] = rtt
        site_state["rtt_base"] = rtt
    else:
        site_state["rtt_ema"] = (
            RTT_ALPHA * rtt + (1.0 - RTT_ALPHA) * site_state["rtt_ema"]
        )


def _try_rtt_slowdown(site_state):
    if site_state["cooldown"] or site_state["rtt_ema"] is None:
        return False
    if site_state["rtt_ema"] <= RTT_HIGH * site_state["rtt_base"]:
        return False
    old_rate = site_state["rate"]
    old_conc = site_state["concurrency"]
    site_state["rate"] = max(MIN_RATE, round(old_rate * RTT_FACTOR, 6))
    site_state["concurrency"] = max(MIN_CONCURRENCY, old_conc - 1)
    site_state["cooldown"] = RTT_COOLDOWN
    _apply_capacity(site_state)
    _record(
        site_state,
        "RTT 偏高（ema=%.3fs 超过基线 %.3fs 的 %.1f 倍）：速率 %.2f→%.2f/s，"
        "并发上限 %d→%d"
        % (site_state["rtt_ema"], site_state["rtt_base"], RTT_HIGH,
           old_rate, site_state["rate"], old_conc,
           site_state["concurrency"]),
    )
    return True


def _try_window_slowdown(site_state):
    if site_state["cooldown"]:
        return
    outcomes = site_state["outcomes"]
    if len(outcomes) < WINDOW_MIN:
        return
    success_rate = sum(outcomes) / len(outcomes)
    if success_rate < SUCCESS_RATE_FLOOR:
        _slow_down(
            site_state,
            "近 %d 次请求成功率 %.0f%% 低于 %.0f%%"
            % (len(outcomes), success_rate * 100, SUCCESS_RATE_FLOOR * 100),
        )


def _try_recover(site_state):
    if site_state["cooldown"] or site_state["ok_streak"] < RECOVER_STREAK:
        return
    if (site_state["rtt_ema"] is not None
            and site_state["rtt_ema"] > RTT_RECOVER * site_state["rtt_base"]):
        return
    old_rate = site_state["rate"]
    old_conc = site_state["concurrency"]
    new_rate = min(MAX_RATE, round(old_rate * RECOVER_FACTOR, 6))
    new_conc = min(MAX_CONCURRENCY, old_conc + 1)
    site_state["ok_streak"] = 0
    if new_rate == old_rate and new_conc == old_conc:
        return
    site_state["rate"] = new_rate
    site_state["concurrency"] = new_conc
    site_state["cooldown"] = RTT_COOLDOWN
    _apply_capacity(site_state)
    _record(
        site_state,
        "连续 %d 次成功且 RTT 正常，缓慢恢复：速率 %.2f→%.2f/s，"
        "并发上限 %d→%d"
        % (RECOVER_STREAK, old_rate, new_rate, old_conc, new_conc),
    )


def _snapshot(site, site_state):
    outcomes = site_state["outcomes"]
    success_rate = (sum(outcomes) / len(outcomes)) if outcomes else 1.0
    ema = site_state["rtt_ema"]
    return {
        "site": site,
        "rate": round(site_state["rate"], 4),
        "concurrency": site_state["concurrency"],
        "in_flight": site_state["in_flight"],
        "tokens": round(site_state["tokens"], 3),
        "rtt_ema": None if ema is None else round(ema, 4),
        "success_rate": round(success_rate, 3),
        "reason": site_state["last_reason"],
        "reasons": list(site_state["reasons"]),
    }


# ---------------- 对外接口 ----------------

def acquire(site):
    """申请一个访问许可，非阻塞；返回调度时刻 at、建议等待秒数 wait 等。"""
    _validate_site(site)
    with _Store() as state:
        s = state["sites"].setdefault(site, _new_site())
        now = state["vclock"]
        # 令牌桶按虚拟时钟补充，容量等于并发上限
        s["tokens"] = min(
            s["concurrency"],
            s["tokens"] + s["rate"] * (now - s["updated_at"]),
        )
        s["updated_at"] = now
        granted = (
            s["in_flight"] < s["concurrency"] and s["tokens"] >= 1.0
        )
        if granted:
            s["tokens"] -= 1.0
            s["in_flight"] += 1
            interval = 1.0 / s["rate"]
            at = max(now, s["next_at"]) + interval
            s["next_at"] = at
            state["vclock"] = at
        else:
            # 并发打满或令牌不足：不推进调度，调用方按 wait 退避后重试
            at = max(now, s["next_at"])
        list.__setitem__(_last, 0, state["vclock"])
        wait = max(0.0, state["wall_origin"] + at - time.monotonic())
        return {
            "site": site,
            "at": round(at, 3),
            "granted": granted,
            "wait": round(wait, 3),
            "rate": s["rate"],
            "concurrency": s["concurrency"],
            "tokens": round(s["tokens"], 3),
            "reason": s["last_reason"],
        }


def release(site, rtt, status):
    """归还许可并上报 RTT 与 HTTP 状态码，触发该站的自适应调整。"""
    _validate_site(site)
    _validate_rtt(rtt)
    _validate_status(status)
    with _Store() as state:
        s = state["sites"].setdefault(site, _new_site())
        s["in_flight"] = max(0, s["in_flight"] - 1)
        # 归还占用的令牌（桶容量=并发上限），速率仍由补充速率与调度间隔约束
        s["tokens"] = min(s["concurrency"], s["tokens"] + 1.0)
        s["cooldown"] = max(0, s["cooldown"] - 1)
        s["outcomes"].append(1 if status < 400 else 0)
        if len(s["outcomes"]) > WINDOW_LIMIT:
            del s["outcomes"][:-WINDOW_LIMIT]

        if status == 429 or status >= 500:
            s["failures"] += 1
            s["ok_streak"] = 0
            s["fail_streak"] += 1
            if s["fail_streak"] >= FAIL_THRESHOLD:
                _slow_down(
                    s,
                    "连续 %d 次错误（status=%d）"
                    % (FAIL_THRESHOLD, status),
                )
                s["fail_streak"] = 0
        else:
            s["successes"] += 1
            s["ok_streak"] += 1
            s["fail_streak"] = 0
            _update_rtt(s, rtt)
            _try_rtt_slowdown(s)
            _try_recover(s)

        # 成功率窗口对每次上报都生效；硬故障刚降速时 cooldown 会使其跳过
        _try_window_slowdown(s)

        list.__setitem__(_last, 0, state["vclock"])
        return _snapshot(site, s)


def status(site=None):
    """输出站点当前速率、并发上限和最近调整原因；site=None 返回全部站点。"""
    with _Store() as state:
        if site is None:
            return {name: _snapshot(name, s)
                    for name, s in state["sites"].items()}
        _validate_site(site)
        s = state["sites"].get(site)
        return None if s is None else _snapshot(site, s)

"""按目标反馈自适应调整扫描速率的调度。

每个目标独立维护一档速率：收到限流提示、响应变慢或错误率上升时降速，
反馈恢复且冷却结束后逐步提回默认速率；连续限流的目标本轮暂停，退避后
从 resume 位置续扫。scan/report 的调用方式保持不变，反馈通过 target 字典
传入：throttle（限流原文）、latency_ms、error_rate、progress、now。
"""

import math
import time

DEFAULT_RATE = 100
MIN_RATE = 10
SLOW_DOWN_FACTOR = 0.5
RECOVERY_FACTOR = 1.25
LATENCY_MS_THRESHOLD = 500
ERROR_RATE_THRESHOLD = 0.2
MAX_ADJUSTMENTS = 10
MAX_CONSECUTIVE_THROTTLES = 3
COOLDOWN_SECONDS = 60
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 1800

_scans = {}


def _blank_record(host, port):
    """每个目标各自独立的速率与续扫记录。"""
    return {
        "target": host,
        "port": port,
        "rate": DEFAULT_RATE,
        "adjustments": 0,
        "throttle": None,
        "resume": 0,
        "progress": 0,
        "paused": False,
        "reason": None,
        "recover_at": None,
        "consecutive_throttles": 0,
        "history": [],
    }


def _view(record, now):
    """返回记录副本，并把绝对恢复时间换算成剩余秒数。"""
    view = dict(record)
    view["history"] = list(record["history"])
    if record["recover_at"] is None:
        view["recover_in_seconds"] = None
    else:
        view["recover_in_seconds"] = max(0, int(round(record["recover_at"] - now)))
    return view


def _record_adjustment(record, now, trigger, before, after, reason, throttle_raw):
    """登记一次实际发生的速率调整，保留前后取值与触发原因。"""
    if before == after or record["adjustments"] >= MAX_ADJUSTMENTS:
        return False
    record["rate"] = after
    record["adjustments"] += 1
    record["history"].append(
        {
            "target": record["target"],
            "at": now,
            "trigger": trigger,
            "before": before,
            "after": after,
            "reason": reason,
            "throttle": throttle_raw,
        }
    )
    return True


def scan(target):
    if not isinstance(target, dict):
        raise ValueError("target 需要字典")
    host = target.get("host")
    if not host:
        raise ValueError("缺少目标地址")
    now = target.get("now") or time.time()

    record = _scans.get(host)
    if record is None:
        record = _blank_record(host, target.get("port", 443))
        _scans[host] = record

    # 续扫位置：以调用方上报的已完成进度为准，暂停期间到达的检查点同样记录，
    # 中断后下一轮从 resume 继续
    progress = target.get("progress")
    if progress is not None:
        progress = int(progress)
        if progress >= record["progress"]:
            record["progress"] = progress
            record["resume"] = progress

    # 暂停中的目标：退避未结束不扫描，结束后自动恢复并从 resume 续扫
    if record["paused"]:
        if now < record["recover_at"]:
            return _view(record, now)
        record["paused"] = False
        record["consecutive_throttles"] = 0
        record["recover_at"] = None
        record["reason"] = "退避结束，恢复扫描"

    throttle_raw = target.get("throttle")
    latency_ms = target.get("latency_ms")
    error_rate = target.get("error_rate")

    triggers = []
    if throttle_raw:
        triggers.append(
            ("throttle", "收到限流提示：%s" % throttle_raw)
        )
    if latency_ms is not None and latency_ms > LATENCY_MS_THRESHOLD:
        triggers.append(
            ("latency", "响应变慢（%sms > %sms 阈值）" % (latency_ms, LATENCY_MS_THRESHOLD))
        )
    if error_rate is not None and error_rate > ERROR_RATE_THRESHOLD:
        triggers.append(
            (
                "error_rate",
                "错误率上升（%.0f%% > %.0f%% 阈值）"
                % (error_rate * 100, ERROR_RATE_THRESHOLD * 100),
            )
        )

    if triggers:
        reason = "；".join(text for _, text in triggers)
        if throttle_raw:
            # 限流原文保留，便于区分目标侧限流与网络问题
            record["throttle"] = throttle_raw
            record["consecutive_throttles"] += 1
        else:
            record["consecutive_throttles"] = 0

        before = record["rate"]
        after = max(MIN_RATE, int(before * SLOW_DOWN_FACTOR))
        if record["adjustments"] >= MAX_ADJUSTMENTS:
            record["reason"] = (
                "触发降速（%s），但调整次数已达上限 %s，速率维持 %s"
                % (reason, MAX_ADJUSTMENTS, before)
            )
        elif _record_adjustment(
            record, now, triggers[0][0], before, after, reason, throttle_raw
        ):
            record["reason"] = reason
        else:
            record["reason"] = "触发降速（%s），已达最低速率 %s" % (reason, MIN_RATE)

        # 降速后进入冷却，届时才允许逐步提速
        record["recover_at"] = now + COOLDOWN_SECONDS

        # 连续限流：暂停本轮，按连续次数指数退避，给出预计恢复时间
        if record["consecutive_throttles"] >= MAX_CONSECUTIVE_THROTTLES:
            backoff = min(
                BACKOFF_MAX_SECONDS,
                BACKOFF_BASE_SECONDS * 2 ** (record["consecutive_throttles"] - 1),
            )
            record["paused"] = True
            record["recover_at"] = now + backoff
            record["reason"] = "连续 %s 次限流，本轮暂停（%s）" % (
                record["consecutive_throttles"],
                reason,
            )
    else:
        # 反馈健康：冷却结束后逐步提速率，最高不超过默认速率
        record["consecutive_throttles"] = 0
        if record["rate"] < DEFAULT_RATE and (
            record["recover_at"] is None or now >= record["recover_at"]
        ):
            before = record["rate"]
            after = min(DEFAULT_RATE, math.ceil(before * RECOVERY_FACTOR))
            if record["adjustments"] >= MAX_ADJUSTMENTS:
                record["reason"] = (
                    "反馈恢复，但调整次数已达上限 %s，速率维持 %s"
                    % (MAX_ADJUSTMENTS, before)
                )
                record["recover_at"] = None
            elif _record_adjustment(
                record, now, "recovery", before, after, "反馈恢复，逐步提速率", None
            ):
                record["reason"] = "反馈恢复，速率 %s -> %s" % (before, after)
                if after >= DEFAULT_RATE:
                    record["recover_at"] = None
                else:
                    record["recover_at"] = now + COOLDOWN_SECONDS
            else:
                record["recover_at"] = None
        elif record["rate"] >= DEFAULT_RATE and record["recover_at"] is not None:
            record["recover_at"] = None

    return _view(record, now)


def report(target=None):
    now = time.time()
    if target is None:
        rows = list(_scans.values())
    else:
        rows = [row for row in _scans.values() if row["target"] == target]
    adjustments = []
    for row in rows:
        adjustments.extend(row["history"])
    return {
        "target": target,
        "count": len(rows),
        "targets": [_view(row, now) for row in rows],
        "adjustments": [dict(entry) for entry in adjustments],
    }

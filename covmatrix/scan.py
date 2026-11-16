"""按目标反馈自适应调整扫描速率，每个目标独立记档，中断后从上次位置继续。"""

import time

DEFAULT_RATE = 100
MIN_RATE = 5
THROTTLE_DOWN_FACTOR = 0.5   # 限流提示时的降速比例
SLOW_DOWN_FACTOR = 0.7       # 响应变慢/错误率上升时的降速比例
RECOVER_STEP = 10            # 每次恢复提升的速率
RECOVER_STEP_SECONDS = 60    # 每档恢复所需秒数，用于预计恢复时间
SLOW_LATENCY_MS = 1000       # 响应变慢阈值
ERROR_RATE_THRESHOLD = 0.1   # 错误率上升阈值
MAX_ADJUSTMENTS = 20         # 同一目标调整次数上限
PAUSE_AFTER_THROTTLES = 3    # 连续限流达到该次数暂停本轮
DEFAULT_TOTAL = 1000         # 未给出总量时的默认扫描位置总数

_scans = {}


def _blank_record(host, port):
    """每个目标独立一档，首次扫描从默认速率开始。"""
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
        "recover_eta": 0,
        "consecutive_throttles": 0,
        "history": [],
    }


def _adjust(record, new_rate, reason):
    """记录一次调整的前后取值和触发原因，超过上限则保持当前速率。"""
    new_rate = max(MIN_RATE, min(DEFAULT_RATE, int(new_rate)))
    if new_rate == record["rate"]:
        return False
    if record["adjustments"] >= MAX_ADJUSTMENTS:
        record["reason"] = "调整次数达到上限 %d，保持当前速率" % MAX_ADJUSTMENTS
        return False
    record["history"].append({
        "from": record["rate"],
        "to": new_rate,
        "reason": reason,
        "at": int(time.time()),
    })
    record["rate"] = new_rate
    record["adjustments"] += 1
    return True


def _update_recover_eta(record):
    """按恢复步进估算回到默认速率的预计秒数。"""
    steps = -(-(DEFAULT_RATE - record["rate"]) // RECOVER_STEP)
    record["recover_eta"] = steps * RECOVER_STEP_SECONDS


def _apply_feedback(record, target):
    hint = target.get("throttle")
    latency = target.get("latency_ms")
    error_rate = target.get("error_rate")
    if hint:
        # 保留限流提示原文，便于判断是目标侧限流还是网络问题
        record["throttle"] = hint
        record["consecutive_throttles"] += 1
        if _adjust(record, record["rate"] * THROTTLE_DOWN_FACTOR, "限流提示: %s" % hint):
            record["reason"] = "目标限流，已降速至 %d" % record["rate"]
        if record["consecutive_throttles"] >= PAUSE_AFTER_THROTTLES:
            record["paused"] = True
            record["reason"] = "连续限流 %d 次，暂停本轮" % record["consecutive_throttles"]
    elif error_rate is not None and error_rate > ERROR_RATE_THRESHOLD:
        record["consecutive_throttles"] = 0
        if _adjust(record, record["rate"] * SLOW_DOWN_FACTOR, "错误率上升: %.2f" % error_rate):
            record["reason"] = "错误率上升，已降速至 %d" % record["rate"]
    elif latency is not None and latency > SLOW_LATENCY_MS:
        record["consecutive_throttles"] = 0
        if _adjust(record, record["rate"] * SLOW_DOWN_FACTOR, "响应变慢: %dms" % latency):
            record["reason"] = "响应变慢，已降速至 %d" % record["rate"]
    else:
        record["consecutive_throttles"] = 0
        if record["paused"]:
            record["paused"] = False
            record["reason"] = "反馈恢复正常，继续扫描"
        if record["rate"] < DEFAULT_RATE:
            if _adjust(record, record["rate"] + RECOVER_STEP, "反馈恢复，逐步提速"):
                record["reason"] = "反馈恢复，逐步提速至 %d" % record["rate"]
    _update_recover_eta(record)


def _view(record):
    """对外视图，隐藏内部计数字段。"""
    return {k: v for k, v in record.items()
            if k not in ("consecutive_throttles", "history")}


def scan(target):
    if not isinstance(target, dict):
        raise ValueError("target 需要字典")
    host = target.get("host")
    if not host:
        raise ValueError("缺少目标地址")
    record = _scans.get(host)
    if record is None:
        record = _blank_record(host, target.get("port", 443))
        _scans[host] = record
    _apply_feedback(record, target)
    if not record["paused"]:
        total = target.get("total", DEFAULT_TOTAL)
        record["resume"] = min(record["resume"] + record["rate"], total)
    record["progress"] = record["resume"]
    return _view(record)


def report(target=None):
    if target is None:
        rows = list(_scans.values())
    else:
        rows = [row for row in _scans.values() if row["target"] == target]
    adjustments = []
    for row in rows:
        for item in row["history"]:
            adjustments.append({
                "target": row["target"],
                "from": item["from"],
                "to": item["to"],
                "reason": item["reason"],
                "at": item["at"],
            })
    return {
        "target": target,
        "count": len(rows),
        "targets": [_view(row) for row in rows],
        "adjustments": adjustments,
    }

"""带可见性超时的持久化文件队列。

消息落盘（JSON，含序号和校验和），取走进入不可见状态并记录超时时刻；
超时未确认自动重投，重投次数超限进入死信；确认幂等，重复确认不报错；
同一消息同一时刻只被一个消费者持有。
"""

import hashlib
import json
import os
import threading
import time

_DEFAULT_DIR = os.environ.get("FILEQUEUE_DIR", ".filequeue")
_DEFAULT_VISIBILITY_TIMEOUT = 1.0
_DEFAULT_MAX_DELIVERIES = 3

_lock = threading.Condition(threading.RLock())
_dir = _DEFAULT_DIR
_visibility_timeout = _DEFAULT_VISIBILITY_TIMEOUT
_max_deliveries = _DEFAULT_MAX_DELIVERIES
_messages = {}
_next_seq = 1


def configure(path=None, visibility_timeout=None, max_deliveries=None):
    """调整队列目录、可见性超时（秒）和重投上限，并从磁盘重新加载。"""
    global _dir, _visibility_timeout, _max_deliveries
    with _lock:
        if path is not None:
            _dir = path
        if visibility_timeout is not None:
            _visibility_timeout = float(visibility_timeout)
        if max_deliveries is not None:
            _max_deliveries = int(max_deliveries)
        _load()
        _lock.notify_all()


def reset():
    """清空队列（内存和落盘文件）。"""
    global _next_seq
    with _lock:
        _messages.clear()
        _next_seq = 1
        if os.path.isdir(_dir):
            for name in os.listdir(_dir):
                if name.endswith(".json"):
                    os.remove(os.path.join(_dir, name))
            try:
                os.rmdir(_dir)
            except OSError:
                pass
        _lock.notify_all()


def put(msg):
    """追加消息并落盘，返回消息序号（即 msg_id）。"""
    global _next_seq
    with _lock:
        record = {
            "seq": _next_seq,
            "payload": dict(msg),
            "status": "visible",
            "redeliveries": 0,
            "invisible_until": None,
            "consumer": None,
        }
        _messages[record["seq"]] = record
        _persist(record)
        _next_seq += 1
        _lock.notify()
        return record["seq"]


def get(consumer, timeout=None):
    """取走队首可见消息并进入不可见状态。

    没有可见消息时阻塞等待，timeout 秒后仍没有则返回 None。
    返回的 dict 附带 "_msg_id"，用于 ack。
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    with _lock:
        while True:
            now = time.time()
            _promote_expired(now)
            record = _oldest_visible()
            if record is not None:
                record["status"] = "invisible"
                record["consumer"] = consumer
                record["invisible_until"] = now + _visibility_timeout
                _persist(record)
                msg = dict(record["payload"])
                msg["_msg_id"] = record["seq"]
                return msg
            delay = _wait_delay(now, deadline)
            if delay is not None and delay <= 0:
                return None
            _lock.wait(delay)


def ack(msg_id):
    """确认消息：仅对不可见（被持有）消息生效，其余情况静默成功（幂等）。"""
    with _lock:
        record = _messages.get(msg_id)
        if record is not None and record["status"] == "invisible":
            del _messages[msg_id]
            _remove(msg_id)
        return True


def stats():
    """输出可见、不可见、死信数量和累计重投次数。"""
    with _lock:
        _promote_expired(time.time())
        counts = {"visible": 0, "invisible": 0, "dead": 0}
        for record in _messages.values():
            counts[record["status"]] += 1
        counts["redeliveries"] = sum(r["redeliveries"] for r in _messages.values())
        return counts


def _oldest_visible():
    visible = [r for r in _messages.values() if r["status"] == "visible"]
    return min(visible, key=lambda r: r["seq"]) if visible else None


def _promote_expired(now):
    """把超时未确认的不可见消息重投为可见，超限的转入死信。"""
    for record in _messages.values():
        if record["status"] != "invisible":
            continue
        if record["invisible_until"] is None or record["invisible_until"] > now:
            continue
        if record["redeliveries"] >= _max_deliveries:
            record["status"] = "dead"
        else:
            record["redeliveries"] += 1
            record["status"] = "visible"
        record["consumer"] = None
        record["invisible_until"] = None
        _persist(record)


def _wait_delay(now, deadline):
    candidates = []
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0.0
        candidates.append(remaining)
    expiries = [
        r["invisible_until"]
        for r in _messages.values()
        if r["status"] == "invisible" and r["invisible_until"] is not None
    ]
    if expiries:
        candidates.append(max(min(expiries) - now, 0.0))
    if not candidates:
        return None
    return min(candidates)


def _checksum(payload):
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _persist(record):
    os.makedirs(_dir, exist_ok=True)
    data = {
        "seq": record["seq"],
        "payload": record["payload"],
        "checksum": _checksum(record["payload"]),
        "status": record["status"],
        "redeliveries": record["redeliveries"],
        "invisible_until": record["invisible_until"],
        "consumer": record["consumer"],
    }
    tmp = os.path.join(_dir, ".tmp-%06d.json" % record["seq"])
    final = os.path.join(_dir, "%06d.json" % record["seq"])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, final)


def _remove(seq):
    try:
        os.remove(os.path.join(_dir, "%06d.json" % seq))
    except FileNotFoundError:
        pass


def _load():
    global _next_seq
    _messages.clear()
    if not os.path.isdir(_dir):
        _next_seq = 1
        return
    for name in os.listdir(_dir):
        if name.startswith(".") or not name.endswith(".json"):
            continue
        with open(os.path.join(_dir, name), encoding="utf-8") as fh:
            data = json.load(fh)
        record = {
            "seq": data["seq"],
            "payload": data["payload"],
            "status": data["status"],
            "redeliveries": data["redeliveries"],
            "invisible_until": data["invisible_until"],
            "consumer": data["consumer"],
        }
        if data.get("checksum") != _checksum(record["payload"]):
            record["status"] = "dead"
        _messages[record["seq"]] = record
    _next_seq = max(_messages, default=0) + 1


_load()

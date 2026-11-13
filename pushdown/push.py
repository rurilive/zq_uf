"""采集结果增量推送：按批次号和水位记录已推送位置，断点续推，确认后推进水位。"""

import copy

MAX_BATCH_ROWS = 10000  # 单批次最大条数，超出拒绝推送

_sent = []  # 模拟下游收件箱（默认传输写入这里）


class PushError(RuntimeError):
    """推送中途失败，携带断点 token 与本次已推条数，供 resume 续推。"""

    def __init__(self, message, token, pushed):
        super().__init__(message)
        self.token = token
        self.pushed = pushed


def _initial_state():
    return {
        "version": 0,     # 全局记录版本号，每条递增
        "next_batch": 1,  # 下一个批次号
        "watermark": 0,   # 已确认水位：连续确认的最大批次号
        "confirmed": [],  # 已确认批次号（允许乱序，水位取连续前缀）
        "pending": [],    # 未确认批次：{"batch", "rows", "offset"}
    }


_state = _initial_state()
_transport = None


def reset():
    """清空全部状态与收件箱，恢复默认传输。"""
    global _state, _transport
    _state = _initial_state()
    _transport = None
    _sent.clear()


def set_transport(transport):
    """注入下游传输函数 transport(row)，抛异常即视为推送失败；None 恢复默认。"""
    global _transport
    _transport = transport


def received():
    """默认传输下模拟下游已收到的记录（含 _version/_batch）。"""
    return list(_sent)


def dump_state():
    """导出可 JSON 序列化的状态，便于重启后复现断点。"""
    return copy.deepcopy(_state)


def load_state(state):
    """恢复 dump_state 导出的状态，结构不合法直接报错。"""
    global _state
    if not isinstance(state, dict):
        raise ValueError("state must be a dict")
    missing = {"version", "next_batch", "watermark", "confirmed", "pending"} - state.keys()
    if missing:
        raise ValueError(f"state missing keys: {sorted(missing)}")
    _state = copy.deepcopy(state)


def _deliver(row):
    if _transport is not None:
        _transport(row)
    else:
        _sent.append(row)


def _find_pending(batch_no):
    for entry in _state["pending"]:
        if entry["batch"] == batch_no:
            return entry
    return None


def _token(batch_no, offset):
    return f"{batch_no}:{offset}"


def _parse_token(token):
    if not isinstance(token, str):
        return None
    parts = token.split(":")
    if len(parts) != 2:
        return None
    try:
        batch_no, offset = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if batch_no < 1 or offset < 0:
        return None
    return batch_no, offset


def _flush(entry):
    """从断点 offset 继续投递，失败抛 PushError（含断点 token），返回本次投递条数。"""
    rows = entry["rows"]
    delivered = 0
    while entry["offset"] < len(rows):
        try:
            _deliver(rows[entry["offset"]])
        except Exception as exc:
            raise PushError(
                f"batch {entry['batch']} interrupted at offset {entry['offset']}: {exc}",
                token=_token(entry["batch"], entry["offset"]),
                pushed=delivered,
            ) from exc
        entry["offset"] += 1
        delivered += 1
    return delivered


def push(batch):
    """推送一批记录：逐条附 _version/_batch，返回 pushed/confirmed/batch/token。"""
    rows = list(batch)
    if not rows:
        raise ValueError("empty batch")
    if len(rows) > MAX_BATCH_ROWS:
        raise ValueError(f"batch too large: {len(rows)} > {MAX_BATCH_ROWS}")
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError(f"row must be a dict, got {type(row).__name__}")
    batch_no = _state["next_batch"]
    _state["next_batch"] += 1
    enriched = []
    for row in rows:
        _state["version"] += 1
        item = dict(row)
        item["_version"] = _state["version"]
        item["_batch"] = batch_no
        enriched.append(item)
    entry = {"batch": batch_no, "rows": enriched, "offset": 0}
    _state["pending"].append(entry)
    _flush(entry)
    return {
        "pushed": len(enriched),
        "confirmed": _state["watermark"],
        "batch": batch_no,
        "token": _token(batch_no, entry["offset"]),
    }


def resume(token=None):
    """断点续推 / 下游确认：token 指向未推完批次则续推，已推完则确认并推进水位。"""
    if token is None:
        return {"pushed": 0, "token": None, "error": "missing token"}
    parsed = _parse_token(token)
    if parsed is None:
        return {"pushed": 0, "token": None, "error": f"unknown token: {token!r}"}
    batch_no, offset = parsed
    if batch_no <= _state["watermark"] or batch_no in _state["confirmed"]:
        return {"pushed": 0, "token": None, "error": f"batch {batch_no} already confirmed"}
    entry = _find_pending(batch_no)
    if entry is None:
        return {"pushed": 0, "token": None, "error": f"unknown token: {token!r}"}
    if offset != entry["offset"]:
        return {"pushed": 0, "token": None,
                "error": f"stale token {token!r}: batch {batch_no} at offset {entry['offset']}"}
    if entry["offset"] < len(entry["rows"]):
        delivered = _flush(entry)
        return {"pushed": delivered, "token": _token(batch_no, entry["offset"]),
                "batch": batch_no, "confirmed": _state["watermark"]}
    _state["pending"].remove(entry)
    _state["confirmed"].append(batch_no)
    while _state["watermark"] + 1 in _state["confirmed"]:
        _state["watermark"] += 1
    return {"pushed": 0, "token": None, "batch": batch_no, "confirmed": _state["watermark"]}

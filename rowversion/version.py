"""行版本写入：乐观并发控制、可配置退避重试与待人工队列。"""

import threading
import time

_lock = threading.Lock()
_rows = {}
_manual = []
_config = {"max_retries": 3, "backoff": 0.05}


class RowVersionError(ValueError):
    """参数非法时抛出，消息指明问题字段。"""


def reset():
    """清空数据、待人工队列并恢复默认配置。"""
    with _lock:
        _rows.clear()
        _manual.clear()
        _config.update(max_retries=3, backoff=0.05)


def configure(max_retries=None, backoff=None):
    """配置冲突重试次数上限和退避基数（秒）。"""
    if max_retries is not None:
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise RowVersionError("max_retries 必须是非负整数")
        _config["max_retries"] = max_retries
    if backoff is not None:
        if not isinstance(backoff, (int, float)) or isinstance(backoff, bool) or backoff < 0:
            raise RowVersionError("backoff 必须是非负数（秒）")
        _config["backoff"] = float(backoff)


def manual_queue():
    """返回重试超限、待人工处理的写入项列表。"""
    with _lock:
        return [dict(item) for item in _manual]


def get(key):
    """返回 {"row": ..., "version": ...}；不存在或已删除返回 None。"""
    with _lock:
        entry = _rows.get(key)
        if entry is None or entry["deleted"]:
            return None
        return {"row": dict(entry["row"]), "version": entry["version"]}


def _validate(key, row, version, merge, limit, base):
    if not isinstance(key, str) or not key:
        raise RowVersionError("key 必须是非空字符串")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise RowVersionError("version 必须是非负整数，新行传 0")
    if row is not None and not isinstance(row, dict):
        raise RowVersionError("row 必须是 dict；删除请传 None")
    if merge is not None and not callable(merge):
        raise RowVersionError("merge 必须是可调用对象")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise RowVersionError("max_retries 必须是非负整数")
    if not isinstance(base, (int, float)) or isinstance(base, bool) or base < 0:
        raise RowVersionError("backoff 必须是非负数（秒）")


def _commit(key, row, version, retries):
    """单次提交：版本匹配则写入，否则返回冲突详情。"""
    with _lock:
        entry = _rows.get(key)
        current_version = 0 if entry is None else entry["version"]
        if current_version != version:
            current = None if entry is None or entry["deleted"] else dict(entry["row"])
            return {"ok": False, "conflict": True, "key": key,
                    "current": current, "version": current_version,
                    "deleted": bool(entry and entry["deleted"]),
                    "retries": retries,
                    "message": "版本冲突：期望 %d，当前 %d" % (version, current_version)}
        deleting = row is None
        if deleting and entry is None:
            return {"ok": False, "error": "not_found", "key": key,
                    "retries": retries, "message": "删除失败：key 不存在"}
        new_version = current_version + 1
        _rows[key] = {"row": None if deleting else dict(row),
                      "version": new_version, "deleted": deleting}
        return {"ok": True, "key": key, "version": new_version,
                "deleted": deleting, "retries": retries}


def update(key, row, version, merge=None, max_retries=None, backoff=None):
    """带版本校验的写入；row 为 None 表示删除（同样校验版本，墓碑防复活）。

    版本不匹配返回 {"ok": False, "conflict": True, "current", "version"}，
    调用方可合并后以返回的版本重试。传 merge(current, mine) 则自动合并重试，
    超过 max_retries 次返回 retry_exhausted 并进入 manual_queue() 待人工。
    """
    limit = _config["max_retries"] if max_retries is None else max_retries
    base = _config["backoff"] if backoff is None else backoff
    _validate(key, row, version, merge, limit, base)
    retries = 0
    candidate = row
    expected = version
    while True:
        result = _commit(key, candidate, expected, retries)
        if result["ok"] or "conflict" not in result:
            return result
        if merge is None:
            return result
        if retries >= limit:
            item = {"key": key, "row": row, "version": version,
                    "attempts": retries, "reason": "retry_exhausted"}
            with _lock:
                _manual.append(item)
            return {"ok": False, "error": "retry_exhausted", "key": key,
                    "attempts": retries, "current": result["current"],
                    "version": result["version"],
                    "message": "重试 %d 次仍冲突，已进待人工队列" % retries}
        retries += 1
        delay = base * (2 ** (retries - 1))
        if delay > 0:
            time.sleep(delay)
        candidate = merge(result["current"], row)
        if candidate is not None and not isinstance(candidate, dict):
            raise RowVersionError("merge 必须返回 dict 或 None")
        expected = result["version"]

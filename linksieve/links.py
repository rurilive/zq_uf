"""采集链接指纹与重访，当前对整串URL取哈希。"""
import hashlib

_seen = {}
_INTERVAL = 3600


def fingerprint(url):
    """整串URL直接哈希，参数顺序和追踪参数都会改变结果。"""
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:16]


def due(url, now):
    """首次到期，之后按固定间隔判断。"""
    fp = fingerprint(url)
    last = _seen.get(fp)
    if last is None:
        _seen[fp] = now
        return {"url": url, "fingerprint": fp, "due": True,
                "interval": _INTERVAL, "reason": "new"}
    ok = now - last >= _INTERVAL
    if ok:
        _seen[fp] = now
    return {"url": url, "fingerprint": fp, "due": ok,
            "interval": _INTERVAL, "reason": "interval"}

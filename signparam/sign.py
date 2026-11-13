"""接口签名，当前参数拼接和算法都写死。"""
import hashlib


def sign(params, rule=None):
    """键排序后直接拼接，再取md5。"""
    parts = ["%s=%s" % (key, params[key]) for key in sorted(params)]
    raw = "&".join(parts)
    return {"sign": hashlib.md5(raw.encode("utf-8")).hexdigest(), "raw": raw}

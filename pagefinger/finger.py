"""页面指纹，当前对整页文本取哈希。"""
import hashlib


def _plain(html):
    return (html or "").strip()


def fingerprint(html):
    """整页文本直接哈希，脚本和广告都会影响结果。"""
    digest = hashlib.sha256(_plain(html).encode("utf-8")).hexdigest()
    return {"hash": digest, "blocks": 1, "confidence": 1.0}


def similar(a, b):
    """只有整页哈希相同才算相似。"""
    return {
        "similar": fingerprint(a)["hash"] == fingerprint(b)["hash"],
        "score": 1.0 if fingerprint(a)["hash"] == fingerprint(b)["hash"] else 0.0,
    }

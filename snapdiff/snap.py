"""页面快照与变更检测，当前整页哈希。"""
import hashlib


def snapshot(page):
    """整页哈希，脚本和计数器都会改变结果。"""
    digest = hashlib.sha256((page.get("html") or "").encode("utf-8")).hexdigest()[:16]
    return {"hash": digest, "blocks": [], "version": 1}


def diff(old, new):
    """只返回是否变化，不定位。"""
    return {"changed": old.get("hash") != new.get("hash"), "blocks": [], "summary": ""}

"""采集条件请求缓存，当前每轮全量下载。"""

_store = {}


def headers(url):
    """不带条件头，每次都要全量。"""
    return {}


def store(url, response):
    """任何响应都覆盖缓存，304 也照写。"""
    body = response.get("body", "")
    _store[url] = {"body": body, "etag": None, "last_modified": None}
    return {"url": url, "stored": True, "status": response.get("status", 200),
            "cached": False, "evicted": 0}

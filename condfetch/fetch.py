"""采集器的请求封装，当前每次都整页下载。"""

_opener = None


def set_opener(func):
    """测试和 demo 用本地实现替换真实请求。"""
    global _opener
    _opener = func


def fetch(url, headers=None):
    """无条件请求，直接返回响应体。"""
    if _opener is None:
        raise RuntimeError("opener 未设置")
    status, body, response_headers = _opener(url, headers or {})
    return body

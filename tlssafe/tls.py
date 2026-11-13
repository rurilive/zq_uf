"""HTTPS请求，当前把所有异常压成一个错误并重试。"""

_opener = None


def set_opener(func):
    global _opener
    _opener = func


class FetchError(Exception):
    def __init__(self, url, reason):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


def fetch(url, retries=1):
    """不区分证书错误和网络错误，失败都重试。"""
    if _opener is None:
        raise RuntimeError("opener 未设置")
    last = None
    for _ in range(retries + 1):
        try:
            return _opener(url)
        except Exception as exc:
            last = exc
    raise FetchError(url, str(last))

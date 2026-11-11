"""失败分类：网络错误退避重试，解析错误记录样本后跳过，永久错误落死信。"""
import urllib.error


class NetworkError(Exception):
    """可重试的网络错误（超时、连接重置、5xx、429 等）。"""


class ParseError(Exception):
    """解析错误：页面结构不符预期。记录样本后跳过，不重试。"""

    def __init__(self, message, sample=""):
        super().__init__(message)
        self.sample = sample


class PermanentError(Exception):
    """永久错误（404、410、权限不足等）：直接落死信，不重试。"""


def classify_http_status(status):
    """HTTP 状态码 -> 'network' | 'permanent'。"""
    if status in (408, 429) or status >= 500:
        return "network"
    if 400 <= status < 500:
        return "permanent"
    return "network"


def classify_error(error):
    """把异常归类为 'network' | 'parse' | 'permanent' | 'unknown'。

    unknown（未传 error 的兼容路径）：立即重试、不计退避，用尽次数后落死信。
    未识别的异常按 network 处理（退避重试，最终仍会落死信，不会无限挂起）。
    """
    if error is None:
        return "unknown"
    if isinstance(error, PermanentError):
        return "permanent"
    if isinstance(error, ParseError):
        return "parse"
    if isinstance(error, NetworkError):
        return "network"
    if isinstance(error, urllib.error.HTTPError):
        return classify_http_status(error.code)
    if isinstance(error, (urllib.error.URLError, OSError, TimeoutError)):
        return "network"
    return "network"

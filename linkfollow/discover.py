"""站内链接提取的最小实现。"""
import re

_LINK = re.compile(r'<a[^>]+href="([^"]+)"')


def discover(page, depth=1):
    return _LINK.findall(page)

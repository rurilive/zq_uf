"""正文指纹，当前对整页取哈希。"""
import hashlib


def fingerprint(html):
    """导航、空白和标点都会改变结果。"""
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def similar(a, b):
    return fingerprint(a) == fingerprint(b)

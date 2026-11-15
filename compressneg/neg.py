"""采集压缩协商，当前固定 gzip 和 utf-8。"""


def headers(site):
    """所有站点都发同一套头。"""
    return {"accept-encoding": "gzip"}


def decode(response):
    """按 utf-8 解码，压缩标记写死。"""
    body = response.get("body", b"")
    return {"text": body.decode("utf-8", "replace"), "encoding": "utf-8",
            "compressed": "gzip", "failed": False}

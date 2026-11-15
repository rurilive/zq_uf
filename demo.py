import gzip

from compressneg import Negotiator


def fetch(site, accept_encoding):
    """模拟站点：只接受 gzip，其余拒绝。"""
    if accept_encoding != "gzip":
        raise RuntimeError("406")
    return {"body": gzip.compress("正文".encode()),
            "headers": {"content-encoding": "gzip",
                        "content-type": "text/html; charset=utf-8"}}


neg = Negotiator(fetch=fetch)
print(neg.selfcheck("shop"))            # 新站点接入自检
print(neg.headers("shop"))              # 按协商结果发请求
print(neg.decode(fetch("shop", "gzip"), site="shop"))

# 解压失败：原始字节保留并单独标出
bad = neg.decode({"body": b"corrupt",
                  "headers": {"content-encoding": "gzip"}}, site="shop")
print(bad["status"], bad["failure_reason"], neg.retained["shop"])

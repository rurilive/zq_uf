import gzip

from compressneg import Negotiator

neg = Negotiator()

# 第一段：首次访问先探测，按站点记住协商结果
print(neg.headers("shop"))  # 未知站点：全量探测
r = neg.decode("shop", {"body": gzip.compress("正文".encode("utf-8")),
                        "headers": {"content-encoding": "gzip"}})
print(r["status"], r["encoding"], r["text"])
print(neg.headers("shop"))  # 已记住：只发 gzip

# 第二段：解压失败保留原始字节并回退 identity；人工可覆盖锁定
bad = {"body": b"\x1f\x8b broken", "headers": {"content-encoding": "gzip"}}
r = neg.decode("mirror", bad)
print(r["status"], r["failure"]["reason"], "raw保留:", r["raw"] == bad["body"])
print(neg.headers("mirror"))  # 回退不压缩
neg.override("mirror", accept_encoding="identity", charset="utf-8")
print(neg.record("mirror")["locked"])  # 人工锁定，不再自动改回

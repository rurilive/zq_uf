import gzip

from compressneg import Negotiator, offer_list

neg = Negotiator()

# 第一段：首次访问先探测，按站点记住协商结果
print("可探测的压缩方式:", offer_list())
print("未知站点 shop:", neg.headers("shop"))  # 全量探测
r = neg.decode("shop", {"body": gzip.compress("正文".encode("utf-8")),
                        "headers": {"content-encoding": "gzip"}})
print("解码:", r["status"], "| 编码:", r["encoding"],
      "| 解压:", r["decompression"], "| 正文:", r["text"])
print("记住后 shop:", neg.headers("shop"))  # 只发协商过的方式

# 第二段：解压失败保留原始字节并回退 identity；人工可覆盖锁定
bad = {"body": b"\x1f\x8b broken", "headers": {"content-encoding": "gzip"}}
r = neg.decode("mirror", bad)
print("解压失败:", r["status"], "| 原因:", r["reason"])
print("原始字节保留:", r["raw"] == bad["body"],
      "| 排查副本:", neg.failed_responses("mirror")[0]["body"] == bad["body"])
print("回退后 mirror:", neg.headers("mirror"))  # 不再声明压缩

neg.override("mirror", accept_encoding="identity", charset="utf-8")
print("人工覆盖并锁定:", neg.record("mirror")["locked"],
      "| 之后:", neg.headers("mirror"))

# 第三段：新站点先跑自检，失败只影响这个站点
print("新站点自检:", neg.selfcheck("fresh")["ok"],
      "| 自检失败站点:", neg.selfcheck(
          "broken", {"body": b"nope",
                     "headers": {"content-encoding": "gzip"}})["ok"])
print("broken:", neg.headers("broken"), "| 其他站点:", neg.headers("another"))

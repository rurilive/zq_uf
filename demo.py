"""compressneg 三种站点的协商演示（本地模拟响应，不发网络请求）。

1. modern  ：支持 gzip，首轮即锁定；
2. legacy  ：不认 accept-encoding，直接回未压缩内容，回退并锁定 identity；
3. flaky   ：首轮 gzip 响应损坏，标记失败并保留原始字节，
             下一轮自动换档，identity 重试成功。
"""

import gzip

from compressneg import decode, headers, profile, reset


def show(title, response):
    print("--- %s" % title)
    print("请求头 :", headers(response["site"]))
    out = decode(response)
    print("协商档 :", out["negotiation"], "| 请求:", out["requested"],
          "| 响应编码:", out["encoding"])
    print("字符集 :", out["charset"],
          "(%s)" % out["charset_source"] if out["charset_source"] else "(未解码)")
    print("解压   :", "失败" if out["failed"] else "成功",
          "| 原因:", out["reason"])
    print("文本   :", out["text"][:40])
    print("缓存   :", profile(response["site"]))
    print()
    return out


def main():
    reset()

    # 站点一：标准 gzip 站点（utf-8 内容，头里声明字符集）
    show("站点1 modern：gzip 正常", {
        "site": "modern",
        "headers": {"Content-Encoding": "gzip",
                    "Content-Type": "text/html; charset=utf-8"},
        "body": gzip.compress("<html>现代站点，gzip 正常</html>".encode()),
    })

    # 站点二：老站点，无视 accept-encoding，返回未压缩 GB18030 内容且无头
    show("站点2 legacy：不支持压缩，缺字符集头，内容探测", {
        "site": "legacy",
        "headers": {},
        "body": "<html><meta charset=gb18030>老站点正文".encode("gb18030"),
    })
    print("legacy 第二轮请求头（应锁定 identity）:", headers("legacy"), "\n")

    # 站点三：gzip 响应损坏 -> 失败标记 + 原始字节保留 -> identity 重试恢复
    show("站点3 flaky：gzip 损坏，失败并保留原始字节", {
        "site": "flaky",
        "headers": {"Content-Encoding": "gzip"},
        "body": b"\x1f\x8b\x08\x00corrupt-gzip-payload",
    })
    show("站点3 flaky：identity 重试恢复", {
        "site": "flaky",
        "headers": {},
        "body": "重试成功后的正文".encode("utf-8"),
    })


if __name__ == "__main__":
    main()

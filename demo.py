"""混合链接巡检演示（离线，使用脚本化假传输）。

覆盖：重复引用去重、跨站点并发、同站点限速、完整重定向链、
重定向环、4xx 死链、超时分类退避、增量导出、按站点汇总。
"""

import os
import tempfile

from linkcheck import check, report, site_report
from linkcheck.transport import FetchError, FetchResponse, CAT_TIMEOUT


class DemoTransport:
    def __init__(self):
        self.calls = 0
        self._routes = {
            "http://news.example/": (301, {"Location": "/home"}),
            "http://news.example/home": (302, {"Location": "https://news.example/index"}),
            "https://news.example/index": (200, {}),
            "http://news.example/missing": (404, {}),
            # 环：a -> b -> a
            "http://loop.example/a": (302, {"Location": "/b"}),
            "http://loop.example/b": (302, {"Location": "/a"}),
        }

    def request(self, url, method="HEAD"):
        self.calls += 1
        if url == "http://slow.example/api":
            raise FetchError(CAT_TIMEOUT, "read timed out")
        rule = self._routes[url]
        status, headers = rule
        return FetchResponse(status, headers, url)


def main():
    transport = DemoTransport()
    export_path = os.path.join(tempfile.mkdtemp(), "incremental.jsonl")

    urls = [
        "http://news.example/",          # 两跳重定向到 200
        "http://news.example/",          # 同一 URL 被第二个页面引用
        "http://news.example/missing",   # 404 死链
        "http://loop.example/a",         # 重定向环
        "http://slow.example/api",       # 超时 -> 退避重试后失败
    ]

    print("== 第一轮巡检：workers=4 ==")
    summary = check(
        urls,
        workers=4,
        transport=transport,
        retries=2,
        backoff=0.02,
        site_interval=0.01,
        export_path=export_path,
    )
    print(summary)
    print(f"实际发出的请求数（去重后）：{transport.calls}")

    print("\n== 死链 / 重定向链 / 环 / 耗时 ==")
    for row in report():
        chain = " -> ".join(
            f"{hop['url']}({hop['status']})" for hop in row["chain"]
        )
        print(
            f"[{row['status']:>8}] {row['url']}  "
            f"http={row['http_status']}  hops={row['hops']}  "
            f"refs={row['references']}  attempts={row['attempts']}  "
            f"elapsed={row['elapsed']:.4f}s"
        )
        if row["status"] in ("redirect", "loop", "redirect_limit"):
            print(f"           chain: {chain}")
        if row["status"] == "loop":
            print(f"           环回到链中第 {row['loop_index']} 跳")
        if row["category"]:
            print(f"           分类: {row['category']}")

    print("\n== 按站点汇总 ==")
    for item in site_report():
        print(item)

    print("\n== 第二轮：增量巡检 + 增量导出 ==")
    # news.example/missing 已巡检过 -> 缓存复用；shop 是新 URL -> 新请求。
    transport._routes["https://shop.example/item/1"] = (200, {})
    again = check(
        ["http://news.example/missing", "https://shop.example/item/1"],
        workers=2,
        transport=transport,
        retries=0,
        site_interval=0.01,
        export_path=export_path,
    )
    print(again)
    print("导出文件内容（每行一条，仅含增量）：")
    with open(export_path, encoding="utf-8") as handle:
        for line in handle:
            print("  " + line.strip()[:110])


if __name__ == "__main__":
    main()

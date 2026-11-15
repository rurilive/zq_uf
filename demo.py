"""模拟连续两轮采集：第一轮全量入库，第二轮条件请求并演示 304 与 LRU 淘汰。"""

from etagcache import get, headers, reset, store

# 小容量便于演示淘汰：每站最多 2 条、全局 5 条
reset(max_per_site=2, max_entries=5)

# 模拟服务端：shop 两页内容不变，news 第二轮更新
SERVER = {
    "https://shop/1": {"body": "shop page 1", "etag": '"s1"',
                       "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
    "https://shop/2": {"body": "shop page 2", "etag": '"s2"',
                       "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
    "https://news/1": {"body": "news page 1 v1", "etag": '"n1-v1"',
                       "last_modified": "Tue, 02 Jan 2024 00:00:00 GMT"},
}


def event_text(result):
    if result["not_modified"]:
        return "304 内容未变"
    if result["updated"]:
        return "更新(内容变化)" if result["changed"] else "更新(内容相同)"
    return "未入库"


def fetch(url, round_no):
    """模拟带条件头的请求：ETag 校验通过返回 304，否则返回 200 全页。"""
    sent = headers(url)
    page = SERVER[url]
    if round_no == 2 and sent.get("If-None-Match") == page["etag"]:
        return {"status": 304, "body": "",
                "headers": {"ETag": page["etag"],
                            "Last-Modified": page["last_modified"]}}
    return {"status": 200, "body": page["body"],
            "headers": {"ETag": page["etag"],
                        "Last-Modified": page["last_modified"]}}


for round_no in (1, 2):
    print(f"===== 第 {round_no} 轮采集 =====")
    if round_no == 2:
        # news/1 第二轮内容更新，ETag 随之变化
        SERVER["https://news/1"] = {"body": "news page 1 v2", "etag": '"n1-v2"',
                                    "last_modified": "Wed, 03 Jan 2024 00:00:00 GMT"}
    for url in SERVER:
        print(f"GET {url} 条件头 -> {headers(url)}")
        result = store(url, fetch(url, round_no))
        print(f"    -> {event_text(result)}, 命中={result['hit']}, "
              f"淘汰={result['evicted_urls']}")

# 第三轮加一个 shop 新页，触发每站 2 条上限的 LRU 淘汰
print("===== 第 3 轮：shop 新增页面触发每站 LRU 淘汰 =====")
# 采集前先检查 shop/1 的条件头，顺便刷新它的 LRU 位置
print(f"GET 前检查 shop/1 条件头 -> {headers('https://shop/1')}")
SERVER["https://shop/3"] = {"body": "shop page 3", "etag": '"s3"',
                            "last_modified": "Thu, 04 Jan 2024 00:00:00 GMT"}
result = store("https://shop/3", {"status": 200, "body": "shop page 3", "headers": {
    "ETag": '"s3"', "Last-Modified": "Thu, 04 Jan 2024 00:00:00 GMT"}})
print(f"GET https://shop/3 -> {event_text(result)}, 淘汰={result['evicted_urls']}")

print("===== 内容保持验证 =====")
print(f"shop/1 经过 304 后内容仍为: {get('https://shop/1')['body']!r}")
print(f"news/1 更新后内容为:       {get('https://news/1')['body']!r}")

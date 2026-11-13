"""混合请求演示：键区分、负缓存、容量淘汰与统计输出。"""

from reqcache import clear, configure, get, make_key, put, report

clear()


def fetch(method, url, headers=None, resp=None):
    """模拟一次请求：命中走缓存，未命中回源并写入缓存。"""
    key = make_key(method, url, headers)
    cached = get(key)
    if cached is not None:
        print("cache ", key)
        return cached
    put(key, resp)
    print("origin", key)
    return resp


# GET 第二次命中；POST 与 GET 不共享键
fetch("GET", "http://shop/1", resp={"status": 200, "body": "page-v1"})
fetch("GET", "http://shop/1", resp={"status": 200, "body": "page-v1"})
fetch("POST", "http://shop/1", resp={"status": 200, "body": "posted"})

# 不同 Accept 头分开缓存，User-Agent 不进键
fetch("GET", "http://shop/2", {"Accept": "application/json"}, {"status": 200, "body": "{}"})
fetch("GET", "http://shop/2", {"Accept": "text/html"}, {"status": 200, "body": "<html>"})
fetch("GET", "http://shop/2", {"accept": "application/json", "User-Agent": "curl"})

# 404 负缓存：第二次不再回源
fetch("GET", "http://shop/missing", resp={"status": 404, "body": "not found"})
fetch("GET", "http://shop/missing", resp={"status": 404, "body": "not found"})

# 小容量下大响应触发 LRU 淘汰
configure(capacity_bytes=1024)
fetch("GET", "http://shop/big1", resp={"status": 200, "body": "x" * 800})
fetch("GET", "http://shop/big2", resp={"status": 200, "body": "y" * 800})

print()
for name, value in report().items():
    print("{}: {}".format(name, value))

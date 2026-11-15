"""模拟连续两轮采集：第一轮全量，第二轮带条件头并处理 304。

运行：``python3 demo.py``
"""

from etagcache import clear, configure, headers, stats, store


class FakeSite:
    """按 URL 保存正文和版本号，模拟采集目标站点。"""

    def __init__(self):
        self.pages = {
            "https://shop/p": ("full page", "v1"),
            "https://shop/q": ("other page", "v1"),
        }

    def fetch(self, url):
        body, version = self.pages[url]
        etag = f'W/"{version}"'
        return {
            "status": 200,
            "body": body,
            "headers": {
                "ETag": etag,
                "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT",
            },
        }

    def touch(self, url, body=None, version=None):
        old_body, old_version = self.pages[url]
        self.pages[url] = (body or old_body, version or old_version)


def collect(site, url, conditional):
    """一次采集：拿到条件头后请求，服务器命中 304，否则回 200。"""
    request_headers = headers(url)
    print(f"  请求头 {request_headers or '（无，全量请求）'}")

    etag = site.fetch(url)["headers"]["ETag"]
    if conditional and request_headers.get("If-None-Match") == etag:
        response = {"status": 304, "body": "", "headers": {"ETag": etag}}
    else:
        response = site.fetch(url)

    result = store(url, response)
    print(f"  {url} -> 状态 {result['status']}，命中 {result['hit']}，"
          f"更新 {result['updated']}，淘汰 {result['evicted']}")
    print(f"  缓存正文 {result['body']!r}，抓取时间 {result['fetched_at']:.3f}")
    return result


def main():
    clear()
    configure(max_entries=100, max_entries_per_site=2)
    site = FakeSite()
    urls = list(site.pages)

    print("第一轮采集（无缓存，全量下载）")
    for url in urls:
        collect(site, url, conditional=False)

    print("\n第二轮采集（带条件头，内容未变，服务器回 304）")
    for url in urls:
        collect(site, url, conditional=True)

    print("\n改动一页内容，再采一次")
    site.touch("https://shop/p", body="changed page", version="v2")
    collect(site, "https://shop/p", conditional=True)
    collect(site, "https://shop/q", conditional=True)

    print("\n容量限制：每站点最多 2 条，继续采集到 3 个页面")
    configure(max_entries_per_site=2)
    site.pages["https://shop/r"] = ("third page", "v1")
    collect(site, "https://shop/r", conditional=False)
    collect(site, "https://shop/p", conditional=True)  # 最久未用，已被淘汰 -> 全量

    print(f"\n统计 {stats()}")


if __name__ == "__main__":
    main()

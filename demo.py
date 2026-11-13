"""多层站点演示：规范化、同站过滤、翻页归并、深度与每层上限。"""
from collections import deque

from linkfollow import discover, Page

SITE_HOST = "site.example"
BASE = "http://%s" % SITE_HOST

SITE = {
    BASE + "/": (
        '<a href="/news?page=2">下一页</a>'
        '<a href="/news?page=3&sid=xyz#tail">下一页变体</a>'
        '<a href="/article/1?ref=home">文章1</a>'
        '<a href="/article/1?ref=top">文章1变体</a>'
        '<a href="/about">about</a>'
        '<a href="http://other.example/ad">外站广告</a>'
    ),
    BASE + "/news?page=2": '<a href="/news/page/3">3</a>',
    BASE + "/news?page=3": '',
    BASE + "/article/1?ref=home": '<a href="/article/2?sessionid=q">文章2</a>',
    BASE + "/article/2": '<a href="/article/3">文章3</a>',
    BASE + "/article/3": '<a href="/article/4">文章4（应被深度上限拦截）</a>',
    BASE + "/about": '',
}


def main():
    queue = deque([(BASE + "/", 1)])
    seen = set()
    crawled = 0

    while queue:
        url, depth = queue.popleft()
        page = Page(SITE[url], url=url, site_host=SITE_HOST)
        result = discover(page, depth=depth, max_depth=3, max_per_level=1)
        crawled += 1

        print("[抓取] depth=%d %s" % (depth, url))
        for link in result.pagination:
            print("  [翻页归并] %s -> 父页面 %s（不计深度、不入队）"
                  % (link, link.parent))
        for link in result:
            if link.dedup_key in seen:
                continue
            seen.add(link.dedup_key)
            queue.append((str(link), link.depth))
            print("  [入队] depth=%d %s key=%s"
                  % (link.depth, link, link.dedup_key))
        for rejected in result.rejected:
            print("  [拒绝] %s reason=%s"
                  % (rejected["url"], rejected["reason"]))

    print("\n抓取页面数: %d，队列剩余: %d；同路径只入队一次、外站不入队、"
          "翻页不单独计深度。" % (crawled, len(queue)))


if __name__ == "__main__":
    main()

"""逐个抓取详情页的最小实现。"""


def crawl(urls, fetch):
    return [fetch(url) for url in urls]

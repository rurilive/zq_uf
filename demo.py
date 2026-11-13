from detailcrawl import crawl

PAGES = {"http://shop/1": "键盘详情", "http://shop/2": "鼠标详情"}
print(crawl(["http://shop/1", "http://shop/2"], PAGES.get))

# TODO: 第二段演示（实现后补）

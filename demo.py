from condfetch import fetch, set_opener

PAGES = {"http://shop/1": "detail-v1"}


def opener(url, headers):
    return 200, PAGES.get(url, ""), {"ETag": '"v1"'}


set_opener(opener)
print(fetch("http://shop/1"))
print(fetch("http://shop/1"))

# TODO: 第二段演示（实现后补）

from tlssafe import FetchError, fetch, set_opener


def opener(url):
    raise ValueError("certificate has expired")


set_opener(opener)
try:
    fetch("https://shop/1")
except FetchError as exc:
    print("失败", exc.url, exc.reason)

# TODO: 第二段演示（实现后补）

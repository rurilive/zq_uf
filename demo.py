from robotscache import allowed, set_fetcher


def fetcher(host):
    print("拉取", host)
    return {"disallow": ["/private"]}


set_fetcher(fetcher)
print(allowed("http://shop/item/1"))
print(allowed("http://shop/item/1"))

# TODO: 第二段演示（实现后补）

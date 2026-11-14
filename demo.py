from contenttype import dispatch

for url in ("http://a/x.json", "http://a/x.xml", "http://a/x"):
    print(url, dispatch({"url": url}))

# TODO: 第二段演示（实现后补）

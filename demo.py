from robotsdiff import allowed, update

update("shop", {"disallow": ["/private"]})
print(allowed("shop", "/private/1"))

# TODO: 第二段演示（实现后补）

from hostmemo import get, remember

remember("shop", {"headers": {"ua": "demo"}, "encoding": "gbk"})
print(get("shop"))
print(get("shop"))

# TODO: 第二段演示（实现后补）

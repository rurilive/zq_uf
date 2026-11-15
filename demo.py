from seedexpand import budget, expand

page = {"url": "https://a/", "links": ["https://a/1", "https://a/2", "https://a/3"]}
print(expand(page, 0))
print(budget("a"))

# TODO: 第二段演示（实现后补）

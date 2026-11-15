from linkrewrite import rewrite, rules

print(rewrite("https://shop/list?utm_source=feed&page=2", "shop"))
print(rules("shop"))

# TODO: 第二段演示（实现后补）

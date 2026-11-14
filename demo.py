from siteallow import allowed, block

block("shop", "403", 60)
print(allowed("shop"))

# TODO: 第二段演示（实现后补）

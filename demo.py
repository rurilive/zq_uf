from maskctl import mask, reveal

print(mask({"phone": "13800000000", "name": "张三"}))
print(reveal({"phone": "138****0000"}, "token-1"))

# TODO: 第二段演示（实现后补）

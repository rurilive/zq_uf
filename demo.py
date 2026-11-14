from tabledesens import mask, reveal

row = {"phone": "13800000000", "city": "sh"}
print(mask(row))
print(reveal(row, "token"))

# TODO: 第二段演示（实现后补）

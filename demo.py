from idemwrite import reset, write

reset()
write([{"key": "a", "v": 2}])
write([{"key": "a", "v": 1}])
print(write([{"key": "a", "v": 1}])["rows"])

# TODO: 第二段演示（实现后补）

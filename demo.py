from sampleci import error, sample

rows = [{"value": i} for i in range(100)]
picked = sample(rows, {"ratio": 0.2})
print(len(picked["rows"]))
print(error(picked))

# TODO: 第二段演示（实现后补）

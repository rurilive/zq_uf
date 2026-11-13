from sampler import sample, summary

rows = [{"id": index, "city": "sh" if index % 2 else "bj", "value": index} for index in range(50)]
picked = sample(rows, {"n": 10})
print(len(picked), summary(picked))

# TODO: 第二段演示（实现后补）
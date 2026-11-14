from metricalert import emit

for i in range(3):
    emit({"job": "job-%d" % i, "reason": "upstream"})
print("sent", 3)

# TODO: 第二段演示（实现后补）

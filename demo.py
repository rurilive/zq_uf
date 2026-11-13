from alertmerge import emit, sent

for index in range(4):
    emit({"job": f"job-{index}", "state": "firing", "reason": "upstream failed"})
print(len(sent()), "条告警")

# TODO: 第二段演示（实现后补）
from jsrender import wait

print(wait({"ready": True}, lambda page: page["ready"]))

# TODO: 第二段演示（实现后补）

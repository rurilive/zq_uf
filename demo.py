from filequeue import ack, get, put

put({"id": 1})
print(get("c1"))
ack(1)
print(get("c2"))

# TODO: 第二段演示（实现后补）

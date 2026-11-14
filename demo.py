from jsonshape import observe, reset

reset()
print(observe("shop", {"id": 1, "price": 2}))
print(observe("news", {"id": 1, "price": 2}))

# TODO: 第二段演示（实现后补）

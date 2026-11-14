from formstate import start, step

state = start("login")
step(state, {"user": "a"})
step(state, {"user": "a"})
print(state)

# TODO: 第二段演示（实现后补）

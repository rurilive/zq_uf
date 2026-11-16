from crashmin import minimize, reproduce

CRASH = {"id": "c1", "data": "CRASH" + "A" * 35, "trigger": "CRASH"}

result = minimize(CRASH)
print(result)
print(reproduce("c1"))
print(reproduce("c404"))

# TODO: 第二段演示（实现后补）

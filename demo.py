from crashmin import minimize, reproduce

CRASH = {"id": "c1", "data": "CRASH" + "A" * 35, "trigger": "CRASH"}

result = minimize(CRASH)
print(result["size"], result["ratio"], result["command"])
print("steps:", len(result["steps"]), "hint:", result["hint"])
print(reproduce("c1"))
print(reproduce("c404"))

# 第二段演示：接近最小的输入会给出提示，避免继续空跑
NEAR_MIN = {"id": "c2", "data": "CRASH" + "A" * 400, "trigger": "CRASH" + "A" * 396}
result2 = minimize(NEAR_MIN)
print(result2["size"], result2["ratio"], result2["hint"])

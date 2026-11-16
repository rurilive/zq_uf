from crashmin import minimize, reproduce

CRASH = {"id": "c1", "data": "CRASH" + "A" * 35, "trigger": "CRASH"}

result = minimize(CRASH)
print(result["size"], result["ratio"], result["data"])
print("original:", result["original"], "steps:", len(result["steps"]))
print("command:", result["command"])
print(reproduce("c1"))
print(reproduce("c404"))

# 已经接近最小的输入：删减比例过低时给出提示，避免继续空跑。
TINY = {"id": "c2", "data": "CRASH", "trigger": "CRASH"}
print(minimize(TINY)["hint"])

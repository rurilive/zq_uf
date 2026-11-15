from formstep import (back, begin, copy_to, export, finish,
                      import_conditions, step)

print(begin("signup"))
print(step("signup", {"name": "a"}))
print(step("signup", {"name": "a"}))

# 第二段：采集批次分步提交（行业 -> 地区 -> 时间范围）
print(begin("batch1"))
r1 = step("batch1", {"industry": "it"})
r2 = step("batch1", {"region": "bj"}, token=r1["token"])
print(step("batch1", {"region": "bj"}, token=r1["token"]))  # 重复提交，判重放
r3 = step("batch1", {"range": "30d"}, token=r2["token"], fail=True)  # 失败保留
print(r3["failed_step"], r3["failed_condition"])
print(step("batch1", {"range": "30d"}, token=r2["token"]))  # 恢复后从原步继续
print(back("batch1", 1))  # 回退到地区步，时间范围作废
print(step("batch1", {"region": "sh"}))
finish("batch1")
saved = export("batch1")
print(import_conditions("batch2", saved))  # 导入新批次，从原步骤继续
print(copy_to("batch1", "batch3"))  # 复制到新批次

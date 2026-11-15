from formstep import begin, step, back, finish, export, copy_flow

# 第一段：分步提交、回退、重复提交
r = begin("signup")
print(step("signup", {"industry": "it"}, token=r["token"]))
print(step("signup", {"region": "bj"}))
r = back("signup", 1)                        # 回退改地区，行业保留
print(r)
print(step("signup", {"region": "bj"}, token="signup-2"))  # 旧令牌重提 -> replayed

# 第二段：失败恢复与批次复用
print(step("signup", {"time": ""}))            # 失败，草稿保留
print(step("signup", {"time": "2026"}))        # 原步骤继续
finish("signup")
print(export("signup"))
print(copy_flow("signup", "signup-2"))         # 复制到新批次复用

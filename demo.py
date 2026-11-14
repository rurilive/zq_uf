from ratemesh import close, configure, give_back, stats, take

configure("render", quota=10, weights={"busy": 1, "idle": 1},
          borrow_cap=4, ttl=30)

take("render", "busy", 5)   # busy 用满自有额度 5
take("render", "busy", 4)   # idle 闲着，busy 借入 4
take("render", "idle", 1)   # idle 自己只用 1
print("忙闲不均：", stats("render"))

give_back("busy", 4)        # 归还借用
give_back("idle", 9)        # 多还只结清持有的 1，不会变负
give_back("idle", 9)        # 已结清，重复归还是幂等空操作
print("归还后：", stats("render"))

take("render", "busy", 4)   # 自有仍占满，再次借入 4
print("父退出回收借用:", close("render"), "笔")
print("重复关闭:", close("render"), "笔")

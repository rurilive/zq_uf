from sessionpin2 import pin

# 第一段：基本绑定与换绑
print(pin.bind("sess-1", "shop"))
print(pin.rebind("sess-1", "shop", "proxy failed"))

# 第二段：有效期、冷却回切、查询与导出
t = 1_000_000
print(pin.bind("sess-2", "shop", now=t))                       # 绑定 proxy-a
pin.mark_failed("proxy-a")
print(pin.bind("sess-2", "shop", now=t + 10))                  # 出口失效，换绑并冷却旧出口
pin.mark_recovered("proxy-a")
print(pin.rebind("sess-2", "shop", "cooldown over",
                 now=t + 10 + pin.COOLDOWN_SECONDS + 1))       # 冷却结束，回切旧出口
print(pin.query("sess-2", now=t + 400))                        # 按会话查绑定
print(pin.export()["history"])                                 # 导出换绑记录，可回看

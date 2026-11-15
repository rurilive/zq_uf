import time

from sessionpin2 import bind, rebind, configure, reset, events

reset()
configure(ttl=0.3, cooldown=0.3, max_bindings=100,
          site_limits={"shop": 10})

print(bind("sess-1", "shop"))   # 首次绑定
print(bind("sess-1", "shop"))   # 有效期内固定同一出口

# 模拟出口失效：重绑到备用出口，记录原因和旧出口
print(rebind("sess-1", "proxy failed"))
print(bind("sess-1", "shop"))   # 保持在新出口

time.sleep(0.4)                 # 绑定过期且旧出口冷却结束
print(bind("sess-1", "shop"))   # 回切原出口

print("\n事件流（绑定/重绑/回切）：")
for e in events():
    print(e)

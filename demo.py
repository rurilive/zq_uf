from sessionpin import bind, configure, rebind, records

# 缩短有效期和冷却，方便演示
configure("shop", ttl=2.0, cooldown=1.0)

print("首次绑定:", bind("session-1", "shop"))
print("有效期内保持:", bind("session-1", "shop"))

# 模拟出口失效：会话降级重绑，而不是整体失败
print("出口失效，重绑:", rebind("session-1", "exit 10.0.0.1:80 unreachable"))

# 站点隔离：同一会话在别的站点独立绑定
print("另一站点绑定:", bind("session-1", "news"))

print("\n绑定/重绑/回切记录:")
for r in records():
    print(" ", r)

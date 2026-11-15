from capticket import TicketSystem

clock = {"t": 1000.0}
sys = TicketSystem(now=lambda: clock["t"], timeout=30, cooldown=60)

# 第一段：优先级分派、账号互斥、回写确认一次
print(sys.submit("acc-1", {"url": "https://shop/login", "priority": 1, "code": "1234"}))
print(sys.submit("acc-1", {"url": "https://shop/pay", "priority": 9, "code": "9999"}))
print(sys.resolve(1, "0000"))   # 回写不匹配，拒绝恢复
print(sys.resolve(1, "1234"))   # 确认恢复，acc-1 接高优工单 2
print(sys.resolve(1, "1234"))   # 重复回写，不再触发

# 第二段：超时回收、站点冷却与转派
clock["t"] += 40                  # 工单 2 超时未回写
print(sys.account_view("acc-1"))  # 账号已释放，shop 站点冷却中，有超时记录
print(sys.reassign(2, account="acc-2"))  # 超时工单转派其他账号
print(sys.resolve(2, "9999"))
clock["t"] += 61                  # 冷却结束，自动恢复接单
print(sys.account_view("acc-1"))

# 第三段：导出与回放
data = sys.export()
clone = TicketSystem.replay(data["events"])
print("exported:", len(data["events"]), "events;",
      "replayed timeouts:", len(clone.export()["timeouts"]))

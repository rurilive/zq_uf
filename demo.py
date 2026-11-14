"""模拟出口抖动：按 (站点, 出口) 三态熔断的完整生命周期。"""

from proxycircuit import acquire, report
from proxycircuit import circuit

circuit.FAILURE_THRESHOLD = 3
circuit.COOLDOWN_SECONDS = 5.0

clock = [0.0]
circuit._now = lambda: clock[0]


def show(tag):
    print("[%s] t=%.0fs 各态计数: %s" % (tag, clock[0], circuit.state_counts()))


print("== shop 站出口 %s 抖动，连续失败达到阈值 ==" % circuit.PROXIES[0])
for _ in range(circuit.FAILURE_THRESHOLD):
    proxy = acquire("shop")
    report("shop", proxy, False)
    print("t=%.0fs %s 失败 -> %s" % (clock[0], proxy, circuit.state_of("shop", proxy)))
show("熔断后")

print("\n== 站点隔离：news 站不受影响 ==")
print("news 获取到:", acquire("news"))
print("shop 获取到（跳过熔断出口）:", acquire("shop"))

print("\n== 冷却结束，只放行一个探针进入半开 ==")
clock[0] += circuit.COOLDOWN_SECONDS
probe = acquire("shop")
print("t=%.0fs 探针出口: %s（状态 %s）" % (clock[0], probe, circuit.state_of("shop", probe)))
print("再次获取（不再放第二个探针）:", acquire("shop"))

print("\n== 探针失败，重新计时 ==")
report("shop", probe, False)
show("探针失败后")
clock[0] += circuit.COOLDOWN_SECONDS - 1
print("冷却未满，shop 获取到:", acquire("shop"))

print("\n== 再次冷却，探针成功恢复关闭 ==")
clock[0] += 1
probe = acquire("shop")
report("shop", probe, True)
print("t=%.0fs 探针 %s 成功 -> %s" % (clock[0], probe, circuit.state_of("shop", probe)))
show("恢复后")

print("\n== 状态迁移 ==")
for site, proxy, old, new, ts in circuit.transitions():
    print("t=%.0fs (%s, %s) %s -> %s" % (ts, site, proxy, old, new))
print("\n== 探针结果 ==")
for site, proxy, ok in circuit.probe_results():
    print("(%s, %s) ok=%s" % (site, proxy, ok))

from crawlwarm import current, dump_state, load_state, reset, warmup
from crawlwarm.warm import COOLDOWN_SECONDS

warmup("shop")
print(current("shop"))

# 第二段：冷启动抖动 -> 回退冷却 -> 阶梯爬升 -> 重启恢复
reset()
site = "shop"
timeline = [
    (0.0, (2, 6)),   # 冷启动抖动，成功率 0.25 -> 回退并冷却
    (5.0, (10, 0)),  # 冷却中，全成功也不升档
    (COOLDOWN_SECONDS + 1, (10, 0)),  # 冷却结束 -> 2
    (COOLDOWN_SECONDS + 2, (9, 1)),   # 成功率 0.9 -> 4
    (COOLDOWN_SECONDS + 3, (10, 0)),  # -> 8
]
for now, window in timeline:
    r = warmup(site, window=window, now=now)
    print(f"t={now:>5.0f} 阶段={r['stage']} 并发={r['concurrency']} "
          f"成功率={r['success_rate']:.2f} 冷却中={r['in_cooldown']}")
print("回退记录:", r["backoffs"])

snapshot = dump_state()   # 模拟进程退出前持久化
reset()                   # 重启，内存状态丢失
load_state(snapshot)      # 从当前档继续
print("重启恢复后并发:", current(site))

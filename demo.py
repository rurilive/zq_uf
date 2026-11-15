"""模拟站点限速：slow 只允许 1 QPS，fast 允许 8 QPS。

以 5 QPS 对两个站点发起采集，观察自适应降速 / 慢涨过程。
用假时钟推进时间，演示无需等待。
"""
import rateadapt.rate as rate
from rateadapt import acquire, feedback, replay, rollback

clock = [1000.0]
rate._now = lambda: clock[0]

LIMIT = {"slow": 1.0, "fast": 8.0}  # 站点每秒允许多少请求
sent = {s: [] for s in LIMIT}


def hit(site):
    """模拟一次请求：最近 1 秒内请求数超限则站点返回 429。"""
    now = clock[0]
    sent[site] = [t for t in sent[site] if now - t < 1.0]
    sent[site].append(now)
    if len(sent[site]) > LIMIT[site]:
        return 429, 0.05
    return 200, 0.2 if site == "slow" else 0.05


for _ in range(400):  # 80 秒模拟
    clock[0] += 0.2
    for site in LIMIT:
        if not acquire(site)["ok"]:
            continue  # 预算耗尽，本秒跳过
        status, rtt = hit(site)
        f = feedback(site, status, rtt)
        if f["adjusted"]:
            print(f"t={clock[0]:6.1f}s {site:4} {f['reason']:>14}"
                  f" -> {f['rate']} QPS (预算 {f['budget']:.1f}/{f['budget_cap']:.1f})")

print("\n最终速率:", {s: acquire(s)["rate"] for s in LIMIT})
print("\nslow 调整回放:")
for h in replay("slow"):
    print(f"  t={h['ts']:6.1f}s {h['from']} -> {h['to']}  ({h['reason']})")
print("\n回滚 slow 上一档:", rollback("slow"))

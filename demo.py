import random

from logsample import (
    begin_batch,
    enable_sampling,
    estimate,
    lines,
    replay,
    reset,
    sampling_off,
    set_rate,
    write,
)

reset()
for index in range(5):
    write({"url": f"/{index}", "status": 200, "slow": index == 3})
print(len(lines()), "行")
print(lines()[3])

# 第二段：分级采样，混合日志
reset()
enable_sampling(0.1)                 # 全局默认比例 10%
set_rate(0.5, site="shop", hour=9)   # shop 站 9 点高峰调到 50%

rng = random.Random(7)
for _ in range(2000):
    write({
        "site": rng.choice(["shop", "blog"]),
        "hour": 9 if rng.random() < 0.5 else 10,
        "url": "/item",
        "status": 500 if rng.random() < 0.02 else 200,
        "slow": rng.random() < 0.01,
    })

batch = begin_batch()
with sampling_off():                 # 异常批次：临时关闭采样，全量保留
    for _ in range(50):
        write({"site": "shop", "hour": 9, "url": "/incident", "status": 200})

print("采样后落盘", len(lines()), "行")
for site, est in sorted(estimate().items()):
    print(site, "精确总量", est["total"], "推算", est["estimated_total"],
          "误差±", round(est["error"], 1), "当前比例", est["rate"])
print("回放异常批次", batch, ":", len(replay(batch)), "行")

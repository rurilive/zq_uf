from logsample import (
    lines,
    replay,
    reset,
    set_rate,
    set_seed,
    stats,
    write,
    write_batch,
)

reset()
for index in range(5):
    write({"url": f"/{index}", "status": 200, "slow": index == 3})
print(len(lines()), "行")
print(lines()[3])

# 第二段：分级采样演示
reset()
set_seed(7)
set_rate("shop", 0.1)            # shop 站正常请求按 10% 采样
set_rate("shop", 0.5, hour=12)   # 午间高峰单独调比例

mixed = []
for i in range(2000):
    mixed.append({"site": "shop", "url": f"/item/{i}", "status": 200, "hour": 9})
for i in range(40):
    mixed.append({"site": "shop", "url": "/pay", "status": 500, "hour": 9})
for i in range(30):
    mixed.append({"site": "shop", "url": "/search", "status": 200,
                  "slow": True, "hour": 9})

batch = write_batch(mixed, batch_id="noon-1")
write_batch([{"site": "shop", "url": "/debug", "status": 200, "hour": 9}] * 50,
            batch_id="hotfix", keep_all=True)   # 异常批次临时关闭采样

print(f"落盘 {len(lines())} 行 / 共 2120 条")
for key, info in stats("shop").items():
    print(key, "比例=%.3f" % info["rate"],
          "精确计数=%d" % info["seen"],
          "估算总量=%.0f±%.0f" % (info["estimated_total"], info["error"]))
print("回放批次", batch, "->", len(replay(batch)), "行；",
      "hotfix ->", len(replay("hotfix")), "行")

from shardbalance import SITES, assign, rebalance

for site in SITES[:4]:
    print(site, assign(site))

before = rebalance()
after = rebalance(["m1", "m2", "m3", "m4"])
print("迁移站点", sum(1 for s in SITES if before[s] != after[s]))

# TODO: 第二段演示（实现后补）

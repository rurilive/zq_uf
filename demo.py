from partitionlease import acquire, renew

acquire("orders", "dt=2026-01-01", "job-a", 30)
acquire("orders", "dt=2026-01-01", "job-b", 30)
print(renew("orders", "dt=2026-01-01", "job-a", 30))

# TODO: 第二段演示（实现后补）

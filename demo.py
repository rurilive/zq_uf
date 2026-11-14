from crawlbarrier import arrive, wait

arrive("batch-1", "shard-a")
arrive("batch-1", "shard-b", ok=False)
print(wait("batch-1"))

# TODO: 第二段演示（实现后补）

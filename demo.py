"""模拟主机失联：租约到期后备机竞选，按最后确认位点续写。"""

from crawlshard import advance, configure, elect, heartbeat, status, write

SHARD = "shard-1"

configure(SHARD, ["m1", "m2", "m3"], lease_ttl=10.0)

print("1. 初始竞选:", elect(SHARD, "m1"))
print("2. 主机写入:", write(SHARD, "m1", {"id": 1, "position": 101}, term=1))
print("3. 心跳续租:", heartbeat(SHARD, "m1"))
print("4. 备机写被拒:", write(SHARD, "m2", {"id": 2}, term=1))

# m1 失联，不再发心跳；时钟推进超过租约 ttl。
advance(11)
print("5. 失联后旧主写被拒:", write(SHARD, "m1", {"id": 2}, term=1))
print("6. 备机竞选:", elect(SHARD, "m2"))
print("7. 新主状态:", status(SHARD, "m2"))
print("8. 从最后位点续写:", write(SHARD, "m2", {"id": 2, "position": 102}, term=2))
print("9. 旧任期写被拒:", write(SHARD, "m1", {"id": 3}, term=1))
print("10. 分片终态:", status(SHARD))

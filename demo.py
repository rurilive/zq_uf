from crawlshard import elect, status, write
from crawlshard import shard

# 第一段：主机持租约写入，备机只读但能看到确认位点
print(elect("shard-1", "m1"))          # m1 成主，term 0
print(elect("shard-1", "m2"))          # m2 为备，可见主已确认的 offset
print(write("shard-1", "m1", {"id": 1}))
print(write("shard-1", "m1", {"id": 2}))
print(write("shard-1", "m2", {"id": 3}))  # 备机只读，拒绝

# 第二段：主机失联，备机竞选，旧主写入被拒，从确认位点续写
shard.expire_lease("shard-1")           # 模拟 m1 失联，租约到期
print(write("shard-1", "m1", {"id": 3}))  # 旧主还在写，旧任期被拒
print(elect("shard-1", "m2"))           # m2 竞选成功，term 1
print(write("shard-1", "m2", {"id": 3}))  # 从 offset 2 续写，不重复抓
print(status("shard-1"))

from crawlshard import elect, write
from crawlshard import shard

print(elect("shard-1", "m1"))
print(elect("shard-1", "m2"))
for i in range(3):
    print(write("shard-1", "m1", {"id": i}))

shard._down.add("m1")
print(elect("shard-1", "m2"))
print(write("shard-1", "m2", {"id": 3}))

shard._down.discard("m1")
print(write("shard-1", "m1", {"id": 99}))

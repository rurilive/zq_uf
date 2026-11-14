import os
import tempfile

from lakechecksum import audit, drop_partition, repair, track, verify

root = tempfile.mkdtemp()
os.environ["LAKECHECKSUM_HOME"] = os.path.join(root, "state")
os.environ["LAKECHECKSUM_BLOCK_SIZE"] = "4"  # 演示用小块

# 第一段：分块校验
path = os.path.join(root, "a.bin")
with open(path, "wb") as handle:
    handle.write(b"abc")
print(verify(path))

# 第二段：模拟坏块并修复
upstream = os.path.join(root, "upstream.bin")
lake = os.path.join(root, "lake.bin")
data = b"0123456789ABCDEF"
for target in (upstream, lake):
    with open(target, "wb") as handle:
        handle.write(data)
track("demo", lake, replica=upstream)

with open(lake, "r+b") as handle:  # 模拟坏块：篡改第 1 块
    handle.seek(4)
    handle.write(b"XXXX")

print("坏块状态:", verify(lake)["block_status"])
print("修复结果:", repair("demo"))
print("修复后:", verify(lake)["block_status"])
print("审计:", audit("demo"))
print("删除分区:", drop_partition("demo"))

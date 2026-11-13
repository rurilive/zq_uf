"""演示：三次内容哈希增量备份 + 任意点恢复。"""
import os
import tempfile

from incbackup import backup, restore

root = tempfile.mkdtemp()
data = os.path.join(root, "data")
store = os.path.join(root, "backups")
os.makedirs(os.path.join(data, "sub"))

with open(os.path.join(data, "a.txt"), "w") as handle:
    handle.write("AAA")
with open(os.path.join(data, "sub", "b.txt"), "w") as handle:
    handle.write("BBB")

print("# 第一次备份：两个文件内容不同 -> 2 个新块")
print(backup(data, store))

print("# 第二次备份：内容未变 -> 0 新增、2 复用")
print(backup(data, store))

with open(os.path.join(data, "a.txt"), "w") as handle:
    handle.write("AAA-VERSION-2")
print("# 第三次备份：a.txt 内容变化 -> 1 新块、1 复用，父快照指向上一次")
third = backup(data, store)
print(third)

print("# 恢复到第三个快照：父链整链校验通过，恢复 2 个文件")
target = os.path.join(root, "restore")
print(restore(third["tag"], target, store))
print("恢复内容 a.txt =", open(os.path.join(target, "a.txt")).read())
print("恢复内容 sub/b.txt =", open(os.path.join(target, "sub", "b.txt")).read())

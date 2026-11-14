import os
import tempfile

from partcompact import compact, query

root = tempfile.mkdtemp()
for i in range(3):
    open(os.path.join(root, f"part{i}.jsonl"), "w").write(f'{{"id": {i}}}\n')

# 1. 模拟合并中断：写入一半时失败，旧文件保留、查询仍可读
def broken_rows():
    yield {"id": 100}
    raise RuntimeError("模拟合并中断")

try:
    compact(root, broken_rows())
except RuntimeError as exc:
    print("合并中断:", exc)
print(" 中断后查询:", query(root))
print("中断后目录:", sorted(os.listdir(root)))

# 2. 正常合并：读旧写新，校验后原子切换清单
result = compact(root, [{"id": 1}, {"id": 2}])
print("合并结果:", result)
print("切换后查询:", query(root))

# 3. 重复合并幂等：内容一致不再切换
print("重复合并:", compact(root, [{"id": 1}, {"id": 2}]))

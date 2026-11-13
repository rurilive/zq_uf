import shutil
import tempfile

from archivepack import pack, restore

root = tempfile.mkdtemp()
pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], root)
print(restore(root))

# 第二批：相同内容去重复用，查询参数被清理，输出新增/复用/失败数
stats = pack([
    {"url": "http://news/a.pdf", "data": b"AAA"},
    {"url": "http://shop/b.pdf?v=2", "data": b"BBB"},
], root)
print({k: stats[k] for k in ("added", "reused", "failed")})
print(restore(root))
shutil.rmtree(root)

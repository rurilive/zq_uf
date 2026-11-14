import tempfile

from pagestore import delete, gc, get, put

root = tempfile.mkdtemp()
put("p1", "same content", root=root)
put("p2", "same content", root=root)
print(get("p2", root=root))

# 重复页面共享块：b 全部复用，对已有页面再写报冲突
big = "重复正文" * 5000
print(put("a", big, root=root))
print(put("b", big, root=root))
print(put("a", "覆盖", root=root))

# 删除只减引用，GC 回收归零块
delete("a", root=root)
delete("b", root=root)
print(gc(root=root))

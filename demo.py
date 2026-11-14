import tempfile

from pagestore import get, put

root = tempfile.mkdtemp()
put("p1", "same content", root=root)
put("p2", "same content", root=root)
print(get("p2", root=root))

# TODO: 第二段演示（实现后补）

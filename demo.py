import shutil
import tempfile

from archivepack import pack, restore

root = tempfile.mkdtemp()
pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], root)
print(restore(root))
shutil.rmtree(root)

# TODO: 第二段演示（实现后补）

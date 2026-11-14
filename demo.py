import tempfile

from crawlmanifest import verify, write

path = write({"rows": 3}, tempfile.mkdtemp())
print(verify(path))

# TODO: 第二段演示（实现后补）

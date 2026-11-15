import tempfile

from manifestcheck import verify, write

with tempfile.TemporaryDirectory() as tmp:
    path = tmp + "/manifest.json"
    print(write({"path": path, "files": ["part-1", "part-2"]}))
    print(verify(path))

# TODO: 第二段演示（实现后补）

import tempfile

from attachcheck import fetch, verify

with tempfile.TemporaryDirectory() as tmp:
    path = tmp + "/demo.bin"
    print(fetch("https://shop/file", path))
    print(verify(path))

# TODO: 第二段演示（实现后补）

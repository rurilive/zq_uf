import os
import tempfile

from filefetch import download

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "report.pdf")
    size = download("http://file/report", path, lambda url: b"%PDF-1.4 demo")
    print("下载字节", size)
    print("落盘", os.path.basename(path), os.path.getsize(path))

# TODO: 第二段演示（实现后补）

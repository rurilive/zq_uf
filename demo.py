import io
import tempfile

from attacharchive import upload

print(upload("a.bin", io.BytesIO(b"payload"), tempfile.mkdtemp()))

# TODO: 第二段演示（实现后补）

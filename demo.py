import os
import tempfile

from lakechecksum import verify

path = os.path.join(tempfile.mkdtemp(), "a.bin")
open(path, "wb").write(b"abc")
print(verify(path))

# TODO: 第二段演示（实现后补）

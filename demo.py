import os
import tempfile

from textnorm import normalize

path = os.path.join(tempfile.mkdtemp(), "win.csv")
open(path, "wb").write(b"\xef\xbb\xbfid,name\r\n1,keyboard\r\n")
result = normalize(path)
print(repr(result["text"]))

# TODO: 第二段演示（实现后补）
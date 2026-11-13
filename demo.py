import os
import tempfile

from csvrepair import repair

path = os.path.join(tempfile.mkdtemp(), "dirty.csv")
open(path, "w", encoding="utf-8").write("1,keyboard\n2,\"mouse,black\",99\n")
print(repair(path, ["id", "name", "price"])["rows"])

# TODO: 第二段演示（实现后补）
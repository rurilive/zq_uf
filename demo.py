import os
import shutil
import tempfile

from fileindex import changed, index, reset

reset()
root = tempfile.mkdtemp()
a = os.path.join(root, "a.parquet")
b = os.path.join(root, "b.csv")
c = os.path.join(root, "c.bin")
open(a, "w").write("AAA")
open(b, "w").write("BBB")
open(c, "w").write("CCC")

print("第一次扫描:", index(root))
print("a 是否变化:", changed(a))

open(b, "w").write("BBBB")
st = os.stat(b)
os.utime(b, (st.st_atime, st.st_mtime + 10))
os.remove(c)
open(os.path.join(root, "d.parquet"), "w").write("DDD")

print("b 是否变化:", changed(b))
print("第二次扫描:", index(root))
print("b 是否变化:", changed(b))

shutil.rmtree(root, ignore_errors=True)

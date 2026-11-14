import os
import tempfile

from filededup import add, objects, refs, remove

root = tempfile.mkdtemp()
store = os.path.join(root, "store")

# 多路径同内容：只新增一个对象，其余复用
for name in ("a.txt", "b.txt", "c.txt"):
    path = os.path.join(root, name)
    open(path, "w").write("same")
    info = add(path, store)
    event = "新增对象" if info["created"] else "复用对象"
    print(event, info["object"][:12], "<-", name, "refcount=", info["refcount"])

# 仍被引用的对象，按对象查引用路径
for digest, obj in objects(store).items():
    print("仍被引用:", digest[:12], "refs=", obj["refcount"], refs(digest, store))

# 删除只减引用，归零才回收对象
for name in ("a.txt", "b.txt", "c.txt"):
    info = remove(os.path.join(root, name), store)
    if info["reclaimed"]:
        print("回收对象", info["object"][:12])
    else:
        print("减引用", info["object"][:12], "剩余", info["refcount"], info["paths"])
print("剩余对象:", objects(store))

import hashlib
import os
import tempfile

from filededup import Store

workdir = tempfile.mkdtemp(prefix="filededup-demo-")
store_root = os.path.join(workdir, "store")
store = Store(store_root)

paths = []
for name, content in (("a.txt", "same"), ("b.txt", "same"), ("c.txt", "other")):
    path = os.path.join(workdir, name)
    with open(path, "w") as handle:
        handle.write(content)
    paths.append(path)

first = store.add(paths[0])
second = store.add(paths[1])
third = store.add(paths[2])
print("新增对象:", first["action"], first["object_id"][:12])
print("同内容复用:", second["action"], second["object_id"][:12])
print("另一个对象:", third["action"], third["object_id"][:12])

shared_id = first["object_id"]
print("对象仍被引用:", store.references(shared_id))

removed_one = store.remove(paths[0])
print("删一个路径，回收:", removed_one["reclaimed"], "剩余引用:", removed_one["references"])

removed_last = store.remove(paths[1])
print("再删最后一个路径，归零回收:", removed_last["reclaimed"])

for entry in store.report()["objects"]:
    digest = hashlib.sha256(b"other").hexdigest()
    print("仍被引用的对象:", entry["object_id"][:12], entry["refcount"], entry["references"])
    assert entry["object_id"] == digest

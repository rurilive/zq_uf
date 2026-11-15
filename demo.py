import os
import tempfile

from manifestcheck import (export_diff, export_manifest, repair, repair_log,
                           verify, write)

SHARDS = (("part-0-a", 0), ("part-0-b", 0), ("part-1-a", 1))

with tempfile.TemporaryDirectory() as tmp:
    data = os.path.join(tmp, "data")
    source = os.path.join(tmp, "source")
    for folder in (data, source):
        os.makedirs(folder)
        for name, _ in SHARDS:
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write((name + "\n").encode() * 10)
    path = os.path.join(tmp, "manifest.json")
    files = [{"name": name, "shard": shard} for name, shard in SHARDS]

    # 第一段：写入与校验
    print(write({"path": path, "data_dir": data, "files": files}))
    print(verify(path, data_dir=data))

    # 第二段：制造损坏，先在一个分片试运行校验，再修复、导出
    os.remove(os.path.join(data, "part-0-a"))
    with open(os.path.join(data, "part-1-a"), "ab") as handle:
        handle.write(b"garbage")
    open(os.path.join(data, "stray.tmp"), "w").close()
    print("分片 0 试运行:", verify(path, data_dir=data, shard=0))
    print("全量校验:", verify(path, data_dir=data))
    print("差异导出:", export_diff(path, os.path.join(tmp, "diff.json"), data_dir=data))
    print("修复:", repair(path, source_dir=source, data_dir=data))
    print("修复记录:", repair_log(path))
    print("分片导出:", export_manifest(path, os.path.join(tmp, "shard-0.json"), shard=0))

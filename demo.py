import os
import tempfile

from auditexport import ExportIntegrityError, export, resume

records = [{"id": i, "ts": 1700000000 + i * 30} for i in range(10)]
spec = {"records": records, "shard_size": 3}
workdir = tempfile.mkdtemp()

# 1) 分片导出：每片一个文件，清单记录每片范围、行数、校验和
path = os.path.join(workdir, "audit.jsonl")
result = export(spec, path)
print("分片导出:", result["rows"], "行", len(result["shards"]), "片",
      "checksum", result["checksum"][:16])

# 2) 模拟导出中断：写两片后崩溃，断点续导只补缺口
path2 = os.path.join(workdir, "audit2.jsonl")
try:
    export(spec, path2, fail_after_shards=2)
except RuntimeError as exc:
    print("导出中断:", exc)
    resumed = resume(exc.token)
    print("断点续导:", resumed["rows"], "行 checksum", resumed["checksum"][:16])
with open(path, encoding="utf-8") as handle:
    expected = handle.read()
with open(path2, encoding="utf-8") as handle:
    assert handle.read() == expected, "续导结果应与一次导出一致"
print("续导结果与一次导出一致")

# 3) 缺片校验：删掉一片后整体校验明确报错
os.remove(os.path.join(path2 + ".shards", "000002.jsonl"))
try:
    export(spec, path2)
except ExportIntegrityError as exc:
    print("整体校验:", exc)

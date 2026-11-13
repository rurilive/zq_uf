"""演示：含脏字段的大数据分片导出，纯标准库，流式写入。"""
import json
import os
import tempfile

from csvexport import export

COLUMNS = ["单号", "身份证", "金额", "备注"]


def big_rows(total=200000):
    for i in range(total):
        yield {
            "单号": "0%06d" % i,
            "身份证": "110101199001%06d" % i,
            "金额": "%.2f" % (i % 1000 + 0.5),
            "备注": '含,逗号 "引号" 和\n换行的脏字段 #%d' % i,
        }


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "orders.csv")
    export(
        big_rows(),
        path,
        {"columns": COLUMNS, "required": ["单号"], "shard_size": 80000},
    )
    manifest_path = os.path.join(tmp, "orders.manifest.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    print("总分片:", len(manifest["shards"]), "总行数:", manifest["total_rows"])
    for shard in manifest["shards"]:
        size = os.path.getsize(shard["file"])
        print(os.path.basename(shard["file"]), shard["rows"], "行", size, "字节")
    head = open(manifest["shards"][0]["file"], "rb").read(120)
    print("首分片开头字节:", head[:3], "(BOM)")
    print(head.decode("utf-8").splitlines()[0])

import os
import tempfile

from auditlog import record, replay

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "audit.jsonl")
    # 遗留计数模式：按文件追加 ok/failed，回放累加
    record({"ok": 2, "failed": 1}, path)
    print("legacy:", replay(path))

    # 批次模式：造一批含失败的数据（k2 报错，k3 返假值），按天写 JSONL
    rows = [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}]

    def sync(row):
        if row["id"] == "k2":
            raise RuntimeError("下游超时")
        return row["id"] != "k3"

    batch_id = record({"records": rows}, handler=sync, directory=tmp)
    print("batch:", batch_id)

    # 只回放失败主键；k2 修复，k3 仍失败
    def repair(row):
        return row["id"] == "k2"

    print("replay 1:", replay(batch_id, repair, directory=tmp))
    # 第二次回放：只剩 k3，修好后全部成功
    print("replay 2:", replay(batch_id, lambda row: True, directory=tmp))

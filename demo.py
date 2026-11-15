"""cdcbatch 演示：原子提交、半途失败反向回滚、人工续跑、幂等与冲突。

每次运行清空 ./.demo-state 后重建，可直接对照目录里的落盘文件。
"""

import json
import os
import shutil

from cdcbatch import BatchProcessor, compute_checksum

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".demo-state")
shutil.rmtree(STATE_DIR, ignore_errors=True)

proc = BatchProcessor(STATE_DIR, batch_size=10)


def row(pos, table, key, values=None, **extra):
    data = {"pos": pos, "table": table, "key": key, "values": values or {}}
    data.update(extra)
    return data


def make(batch_id, seq, pos, rows):
    batch = {"id": batch_id, "seq": seq, "pos": pos, "rows": rows}
    batch["checksum"] = compute_checksum(batch)
    return batch


def show(title, result):
    print("== %s ==" % title)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print()


# 1) 正常批次：整批成功才推进位点
show(
    "批次1 原子提交",
    proc.apply(
        make("b1", 1, 0, [
            row(0, "users", "u1", {"name": "alice", "age": 30}),
            row(1, "users", "u2", {"name": "bob", "age": 25}),
        ])
    ),
)

# 2) 半途失败：第 3 行失败，前两行按反向顺序回滚，位点不推进，批次落盘 pending
show(
    "批次2 半途失败 -> 反向回滚",
    proc.apply(
        make("b2", 2, 2, [
            row(2, "orders", "o1", {"amount": 100}),
            row(3, "orders", "o2", {"amount": 200}),
            row(4, "orders", "o3", bad=True),  # 模拟下游写入失败
        ])
    ),
)
print("回滚后 orders 表（应为空）:", proc._load_table("orders"))
print("pending 文件:", os.listdir(os.path.join(STATE_DIR, "pending")))
print()

# 3) 人工修复 pending 批次后用同一令牌续跑
pending_path = proc.pending_file("b2")
with open(pending_path, encoding="utf-8") as handle:
    pending = json.load(handle)
pending["batch"]["rows"][2].pop("bad")
pending["batch"]["rows"][2]["values"] = {"amount": 300}
pending["batch"]["checksum"] = compute_checksum(pending["batch"])
with open(pending_path, "w", encoding="utf-8") as handle:
    json.dump(pending, handle, ensure_ascii=False)
show("批次2 人工修复后续跑", proc.resume("b2"))

# 4) 重复批次：相同批次号整批按位点跳过，不重复写入
b1 = make("b1", 1, 0, [
    row(0, "users", "u1", {"name": "alice", "age": 30}),
    row(1, "users", "u2", {"name": "bob", "age": 25}),
])
show("批次1 重复提交 -> 按位点跳过", proc.apply(b1))

# 5) 冲突：同 key 字段值不一致，两侧原始值都保留，当前行不被覆盖
show(
    "批次3 冲突双侧保留",
    proc.apply(
        make("b3", 3, 5, [
            row(5, "users", "u1", {"name": "alice2", "age": 99}),
        ])
    ),
)
print("冲突后 users/u1 维持原值:", proc._load_table("users")["u1"]["values"])
print()
print("最终位点 watermark:", proc.state["watermark"])
print("已提交批次:", sorted(proc.state["batches"], key=int))
print("落盘文件:")
for root, _dirs, files in os.walk(STATE_DIR):
    for name in sorted(files):
        print(" ", os.path.relpath(os.path.join(root, name), STATE_DIR))

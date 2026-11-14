"""演示：快照期间写入被缓存，提交后按位点对齐补应用。"""

import json
import os
import tempfile

from cdcsnapshot import SnapshotStore, check_consistency

# 演示用独立位点文件，跑完即删
tmp = tempfile.TemporaryDirectory()
position_file = os.path.join(tmp.name, "cdc_position.json")
store = SnapshotStore(position_file=position_file)
table = "orders"

# 1) 快照前的变更，位点推进到 2
print("== 快照前变更 ==")
print(
    store.apply_events(
        [
            {"key": "a", "row": {"v": 1}, "pos": 1},
            {"key": "b", "row": {"v": 2}, "pos": 2},
        ]
    )
)

# 2) 开启快照：记录起始位点，窗口内写入只缓存
print("\n== 快照窗口内写入（被缓存）==")
store.begin_snapshot(table)
print(
    store.apply_events(
        [
            {"key": "c", "row": {"v": 3}, "pos": 3},
            {"key": "a", "row": {"v": 10}, "pos": 4},
        ]
    )
)

# 3) 提交快照：缓存按序补应用，位点推进到 4，并输出校验结果
print("\n== 提交快照 ==")
snap = store.commit_snapshot()
print(json.dumps(snap, ensure_ascii=False, indent=2))

# 4) 模拟重放：从最后确认位点开始，旧事件被跳过，保证幂等
print("\n== 重放旧事件（幂等）==")
print(
    store.apply_events(
        [
            {"key": "a", "row": {"v": 10}, "pos": 4},
            {"key": "d", "row": {"v": 4}, "pos": 5},
        ]
    )
)

# 5) 恢复：新存储从共用位点文件加载，行数据来自快照，确认位点为 5
print("\n== 从位点文件恢复后重放 ==")
recovered = SnapshotStore(
    position_file=position_file, rows=dict(store.rows)
)
print("恢复确认位点:", recovered.position)
print(
    recovered.apply_events(
        [{"key": "a", "row": {"v": 10}, "pos": 4}]
    )
)

# 6) 一致性校验：行数 + 校验和一致则通过，不一致时输出差异行
print("\n== 一致性校验 ==")
# 先按当前数据重算一份基线，校验通过
fresh = store.snapshot(table)
print(store.check_consistency(table, fresh))
# 再拿 pos=4 的旧快照对比：pos=5 新增的 d 行会作为差异行报出
bad = recovered.check_consistency(
    table,
    {
        "rows": snap["check"]["rows"],
        "checksum": snap["check"]["checksum"],
        "data": dict(snap["data"]),
    },
)
print("与旧快照不一致时的差异行:", json.dumps(bad["diff"], ensure_ascii=False))

tmp.cleanup()

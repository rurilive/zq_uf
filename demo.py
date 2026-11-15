"""两批采集结果的合并演示：版本、来源优先级、冲突、回放与导出。"""

import json
import tempfile
import os

from crawlmerge2 import merge, report, replay, export

# 来源优先级可配置：越靠前优先级越高。
SOURCE_PRIORITY = ["official", "mirror"]

first = {
    "batch": "batch-1",
    "time": "2026-11-01T08:00:00",
    "rows": [
        {"key": "a", "version": 1, "source": "mirror", "value": "a-v1"},
        {"key": "b", "version": 2, "source": "official", "value": "b-v2"},
        {"key": "c", "version": 1, "source": "official", "time": "2026-11-01T09:00:00", "value": "c-morning"},
        {"key": "d", "source": "mirror", "value": "d-no-version"},
    ],
}

second = {
    "batch": "batch-2",
    "time": "2026-11-10T08:00:00",
    "rows": [
        # 高版本胜出，即便来自后到批次之外的低优先来源。
        {"key": "a", "version": 3, "source": "mirror", "value": "a-v3"},
        # 同版本：official 优先级高于 mirror。
        {"key": "b", "version": 2, "source": "mirror", "value": "b-override"},
        # 同来源不同批次：按时间排序，晚上的记录胜出。
        {"key": "c", "version": 1, "source": "official", "time": "2026-11-01T18:00:00", "value": "c-evening"},
        # 缺版本按基线 0 补齐，缺版本行之间按来源优先级补齐判定。
        {"key": "d", "source": "official", "value": "d-filled"},
        # 同版本、同优先级、不同来源、内容不同 => 冲突，两侧原始行都保留。
        {"key": "e", "version": 1, "source": "left", "value": "e-left"},
        {"key": "e", "version": 1, "source": "right", "value": "e-right"},
    ],
}

print("== merge(batches) 汇总 ==")
result = merge([first, second], source_priority=SOURCE_PRIORITY)
print(json.dumps(
    {"merged": result["merged"],
     "conflict_keys": [c["key"] for c in result["conflicts"]],
     "dropped": [(d["key"], d["reason"]) for d in result["dropped"]]},
    ensure_ascii=False, indent=2))

print("\n== report() 逐行来源与判定 ==")
for row in report()["merged"]:
    filled = " (version_filled)" if row["version_filled"] else ""
    print(f"{row['key']}: source={row['source']}, version={row['version']}{filled}, "
          f"decision={row['decision']}, batch={row['batch']}")

print("\n== 冲突明细：两侧原始行均保留 ==")
for conflict in report()["conflicts"]:
    print(f"key={conflict['key']} reason={conflict['reason']}")
    for side in conflict["sides"]:
        print(f"  - {side['source']} @ {side['batch']}: {side['raw']}")

print("\n== replay()：按同一规则重算，结果与首次合并一致 ==")
print("replay == merge:", replay() == result)

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "merge-export.json")
    export(path)
    replayed = replay(path)
    print(f"== export() 导出 {os.path.basename(path)} 后跨进程回放一致: "
          f"{replayed == result} ==")

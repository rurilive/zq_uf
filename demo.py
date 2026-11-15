"""冷热分层试运行演示（单表 orders）。

运行：python3 demo.py
"""

import tempfile
import os

from coldhot import (
    reset,
    seed,
    archive,
    query,
    restore,
    confirm_cleanup,
    list_archives,
    export_restores,
    manager,
)


def show(title, payload):
    print(f"\n===== {title} =====")
    print(payload)


def main():
    reset()
    seed("orders", [
        {"order_id": 1, "ts": 1, "status": "paid", "amount": 100},
        {"order_id": 2, "ts": 2, "status": "paid", "amount": 200},
        {"order_id": 3, "ts": 3, "status": "unpaid", "amount": 300},
        {"order_id": 4, "ts": 4, "status": "paid", "amount": 400},
        {"order_id": 5, "ts": 5, "status": "paid", "amount": 500},
    ])

    # 1) 按时间归档：记录位置、范围、行数与内容校验和
    arc = archive("orders", {"start": 1, "end": 3})
    show("1. 归档（ts 1~2 转冷层，含位置/范围/校验和）", arc)

    # 2) 业务不改任何查询条件，框架自动路由在线+冷层并合并
    merged = query("orders", {"status": "paid"})
    show("2. 透明查询（自动走 online+cold 并合并）", {
        "routed": merged["routed"],
        "tiers": merged["tiers"],
        "sources": merged["sources"],
        "complete": merged["complete"],
        "order_ids": [row["order_id"] for row in merged["rows"]],
    })

    # 3) 冷层不可用：给出提示而不是返回空结果
    manager().cold.available = False
    degraded = query("orders", {"status": "paid"})
    show("3. 冷层故障（降级提示，在线数据照给，明确标注不完整）", {
        "degraded": degraded["degraded"],
        "complete": degraded["complete"],
        "warnings": degraded["warnings"],
        "sources": degraded["sources"],
        "order_ids": [row["order_id"] for row in degraded["rows"]],
    })
    manager().cold.available = True

    # 4) 恢复：按归档记录回迁，校验行数与内容，冷层副本先保留
    restored = restore(archive_id=arc["archive_id"])
    show("4. 恢复（行数+内容校验通过，副本保留待人工清理）", restored)

    # 5) 校验失败可重试：注入内容损坏 -> 恢复失败留痕 -> 修复后重试成功
    bad_arc = archive("orders", {"start": 4, "end": 6})
    manager().cold.corrupt_shard(bad_arc["location"])
    failed = restore(archive_id=bad_arc["archive_id"])
    show("5a. 恢复校验失败（副本保留、在线不被污染、可重试）", {
        "result": failed["result"],
        "status": failed["status"],
        "verification": failed["verification"],
    })
    manager().cold.put(bad_arc["location"], [
        {"order_id": 4, "ts": 4, "status": "paid", "amount": 400},
        {"order_id": 5, "ts": 5, "status": "paid", "amount": 500},
    ])
    retried = restore(archive_id=bad_arc["archive_id"])
    show("5b. 修复后重试恢复成功", {
        "result": retried["result"],
        "status": retried["status"],
    })

    # 6) 人工确认后才清理冷层副本
    cleanup = confirm_cleanup(archive_id=arc["archive_id"])
    show("6. 人工确认清理第一份冷层副本", cleanup)

    # 7) 归档范围可查询，恢复记录可导出
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "restore_records.json")
        export_restores(path=path)
        show("7. 归档范围 + 恢复记录导出文件", {
            "archives": list_archives("orders"),
            "export_path": path,
        })

    # 8) 全程透明性回归：最终查询结果与原始 5 行一致、无重复
    final = query("orders", {})
    show("8. 最终一致性回归", {
        "count": final["count"],
        "order_ids": [row["order_id"] for row in final["rows"]],
        "complete": final["complete"],
    })


if __name__ == "__main__":
    main()

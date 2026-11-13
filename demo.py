"""两次分页快照的增量监控演示：建基线 → 下一次运行出增量并触发告警。"""

import os
import tempfile

from listwatch import ChangeThresholdExceeded, Store

# 第一次抓取：两页，共 5 条（去重后）
SNAPSHOT_ONE = [
    [
        {"id": "101", "title": "发布指南", "status": "online"},
        {"id": "102", "title": "更新日志", "status": "online"},
        {"id": "103", "title": "常见问题", "status": "online"},
    ],
    [
        {"id": "103", "title": "常见问题", "status": "online"},  # 分页边界重复
        {"id": "104", "title": "价格说明", "status": "online"},
        {"id": "105", "title": "联系我们", "status": "online"},
    ],
]

# 第二次抓取：更新 / 下架 / 真删除 / 重排 / 新增，标题仅改标点不算变更
SNAPSHOT_TWO = [
    [
        {"id": "102", "title": "更新日志！", "status": "online", "views": 99},
        {"id": "104", "title": "价格说明", "status": "offline"},
        {"id": "101", "title": "发布指南", "status": "online"},
    ],
    [
        {"id": "103", "title": "常见问题", "status": "online"},
        {"id": "106", "title": "新活动公告", "status": "online"},
    ],  # 105 完全消失 = 真删除
]


def show(result):
    print("站点:", result["site"], "| 基线:", result["baseline"])
    print("  新增:", [item["title"] for item in result["added"]])
    print("  更新:", [item["title"] for item in result.get("updated", [])])
    print("  下架:", [item["title"] for item in result.get("delisted", [])])
    print("  删除:", [item["title"] for item in result["removed"]])
    print("  重排:", [(entry["title"], entry["old_index"], entry["new_index"])
                       for entry in result.get("reordered", [])])
    print("  变更量:", result["change_count"])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "listwatch_state.json"))

        print("== 第一次运行：建立基线 ==")
        show(store.run("demo-news", SNAPSHOT_ONE))

        print("\n== 第二次运行：分页快照增量比对（阈值 3） ==")
        try:
            show(store.run("demo-news", SNAPSHOT_TWO, alert_threshold=3))
        except ChangeThresholdExceeded as alert:
            show(alert.result)
            print("  告警:", alert)


if __name__ == "__main__":
    main()

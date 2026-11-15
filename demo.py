"""演示：批量任务同时失败后，站点 + 全局两级配额如何限制重试洪峰。

运行：``python3 demo.py``
"""

import json
import os
import tempfile

from retryquota import QuotaCoordinator


def show(title, result):
    print(f"  {title:<28} {json.dumps(result, ensure_ascii=False)}")


def main():
    state_path = os.path.join(tempfile.gettempdir(), "retryquota-demo-state.json")
    try:
        os.unlink(state_path)
    except FileNotFoundError:
        pass
    quota = QuotaCoordinator(
        global_limit=4,       # 全局最多 4 个重试许可
        site_limit=2,         # 每个站点最多 2 个
        site_limits={"pay": 1},
        lease_seconds=30,
        priorities={"task-urgent": 10},
        state_path=state_path,
    )

    print("== 第一段：批量失败，超额排队 ==")
    tasks = [
        ("task-urgent", "shop"),
        ("task-1", "shop"),
        ("task-2", "shop"),
        ("task-3", "shop"),
        ("task-4", "api"),
        ("task-5", "api"),
        ("task-6", "pay"),
        ("task-7", "pay"),
    ]
    for task, site in tasks:
        result = quota.request(task, site)
        show(
            f"{task}@{site}",
            {
                "granted": result["granted"],
                "reason": result.get("blocked_by", result["reason"]),
                "position": result["position"],
            },
        )

    state = quota.snapshot()
    print(
        f"\n  全局持有 {state['active_total']}/{state['limits']['global']}，"
        f"排队 {state['queued_total']} 个"
    )
    for site, info in sorted(state["sites"].items()):
        print(
            f"  站点 {site}: 持有 {info['active']}/{info['limit']}，排队 {info['queued']}"
        )

    print("\n== 第二段：释放后按优先级出队 ==")
    show("release(task-urgent)", _brief(quota.release("task-urgent")))
    show("release(task-1)", _brief(quota.release("task-1")))
    show("release(task-1) 重复释放", _brief(quota.release("task-1")))

    print("\n== 第三段：失败任务不占名额 ==")
    show("fail(task-4)", _brief(quota.fail("task-4")))
    show("fail(task-6) 排队中失败", _brief(quota.fail("task-6")))

    print("\n== 第四段：最终状态（任何进程都可查看）==")
    state = quota.snapshot()
    summary = {
        "active_total": state["active_total"],
        "queued_total": state["queued_total"],
        "active": [item["task"] for item in state["active"]],
        "queue": [
            f"{entry['position']}.{entry['task']}({entry['priority']})"
            for entry in state["queue"]
        ],
        "sites": state["sites"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    print("\n== 第五段：事件流（发放 / 排队 / 释放 / 超时 / 失败）==")
    for event in quota.events(limit=6):
        print(f"  {event['event']:<16} {event.get('task', '')}")
    print(f"\n  状态文件：{state_path}")
    print(f"  跨进程查看：python3 -m retryquota {state_path} --events 5")


def _brief(result):
    if "granted_from_queue" in result:
        result = dict(result)
        result["granted_from_queue"] = [
            item["task"] for item in result["granted_from_queue"]
        ]
    return result


if __name__ == "__main__":
    main()

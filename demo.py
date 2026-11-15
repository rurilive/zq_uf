"""演示：越权站点被拒、重复任务被拒、名单热更新原子生效。"""

import json
import os
import tempfile

from crawlguard import Guard

LIST = {
    "version": "2026-11-15.1",
    "effective_at": "2026-01-01T00:00:00Z",
    "sources": {"allow": ["cms-*"], "deny": ["cms-rogue"]},
    "sites": {"allow": ["shop", "news"], "deny": ["*.internal"]},
}

LIST_V2 = {
    "version": "2026-11-15.2",
    "effective_at": "2026-01-01T00:00:00Z",
    "sources": {"allow": ["cms-*"], "deny": ["cms-rogue"]},
    "sites": {"allow": ["shop"], "deny": ["*.internal"]},
}


def write(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def show(label, result):
    print(f"{label}: {result['decision']} reason={result['reason'] or '-'} "
          f"version={result['list_version']}")


with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "list.json")
    write(path, LIST)
    guard = Guard(path)
    print("reload:", guard.reload())

    show("正常任务   ", guard.admit({"id": "task-1", "source": "cms-a", "site": "shop", "url": "/p/1"}))
    show("重复任务   ", guard.admit({"id": "task-1-again", "source": "cms-a", "site": "shop", "url": "/p/1"}))
    show("越权站点   ", guard.admit({"id": "task-2", "source": "cms-a", "site": "admin.internal"}))
    show("越权来源   ", guard.admit({"id": "task-3", "source": "cms-rogue", "site": "shop"}))

    print("\n拒绝审计:")
    for entry in guard.audit_log():
        print(f"  {entry['task']}: {entry['reason']} — {entry['reason_text']} "
              f"(version={entry['list_version']})")

    print("\n按站点汇总:", json.dumps(guard.summary()["sites"], ensure_ascii=False))
    print("导出拒绝:", guard.export_rejections(os.path.join(tmp, "rejections.json"))["count"], "条")

    print("\n--- 热更新：v2 收紧站点白名单 ---")
    write(path, LIST_V2)
    print("reload:", guard.reload())
    show("原放行站点 ", guard.admit({"id": "task-4", "source": "cms-a", "site": "news"}))
    print("按站点汇总:", json.dumps(guard.summary()["sites"], ensure_ascii=False))

    print("\n--- 热更新失败：坏名单保留旧版本 ---")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{broken")
    print("reload:", guard.reload())
    show("旧名单仍生效", guard.admit({"id": "task-5", "source": "cms-a", "site": "shop"}))

import json

from privpath import config_changed, record_reproduction, report, reset, walk

OPS = [
    {
        "op": "read-config",
        "needs": "user",
        "effect": "admin",
        "preconditions": ["config-portal-open"],
        "config": ["svc.config_portal"],
    },
    {
        "op": "dump-db",
        "needs": "admin",
        "effect": "root",
        "preconditions": ["backup-enabled"],
        "config": ["db.backup_job"],
        "account": "svc-backup",
    },
    {"op": "open-shell", "needs": "root", "effect": "host"},
    {"op": "sudo-reload", "needs": "admin", "effect": ""},
]


def show(title, data):
    print("=" * 8, title, "=" * 8)
    print(json.dumps(data, ensure_ascii=False, indent=2))


reset()

# 第一段：正常多跳，user -> admin -> root -> host
show("walk(dev, user)", walk({"account": "dev", "role": "user", "ops": OPS}))
show("walk(ops, admin)", walk({"account": "ops", "role": "admin", "ops": OPS}))

# 第二段：前置条件不满足、配置变更、跨账号复现失败
show(
    "walk(staging, precondition off)",
    walk(
        {
            "account": "staging",
            "role": "user",
            "ops": OPS,
            "conditions": {"config-portal-open": False},
        }
    ),
)

show("config changed: db.backup_job", config_changed(["db.backup_job"]))

record_reproduction(
    "dev",
    "dev-staging",
    "root",
    "staging 关闭 db.backup_job，dump-db 不可用，原 dev->root 结论保留",
)

show("report()", report())

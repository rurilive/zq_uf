"""演示：编排式版本切换，连续切两次版本（升级再回滚）。"""

from versionflow import (
    SwitchError,
    audit_log,
    cache_keys,
    put_cache,
    register_dataset,
    reset,
    switch,
)


def show(result):
    print(f"[switch] {result['dataset']}: {result['from']} -> {result['to']}")
    for step in result["step_results"]:
        print(f"  step {step['step']}: {step['status']} - {step['detail']}")
    print(f"  final_version={result['final_version']}")


reset()
register_dataset("orders", versions=["v1", "v2"], current="v1")
put_cache("orders", "feature:orders:7d", {"rows": 1024})

show(switch("orders", "v2", operator="ops-chen", reason="升级到 v2"))
print("cache after upgrade:", cache_keys("orders"))

put_cache("orders", "feature:orders:v2", {"rows": 2048})
show(switch("orders", "v1", operator="ops-chen", reason="v2 指标异常，回滚"))
print("cache after rollback:", cache_keys("orders"))

try:
    switch("orders", "v9", operator="ops-chen", reason="演示未知版本")
except ValueError as exc:
    print("error:", exc)

for entry in audit_log():
    print(
        "audit:",
        entry["operator"],
        f"{entry['from']}->{entry['to']}",
        entry["status"],
        entry["reason"],
    )

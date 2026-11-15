"""演示：快照克隆、克隆中断后从断点续传、行数/校验和校验。"""

import json
import shutil
import tempfile

import partclone
from partclone import (
    CloneError,
    Partition,
    audit_log,
    clone,
    load_checkpoint,
    query,
    reset,
    verify,
)

# 演示用独立状态目录，跑完即删
state_dir = tempfile.mkdtemp(prefix="partclone-demo-")


def _rows(count):
    return [{"id": index, "value": index * 10} for index in range(count)]


try:
    print("== 1. 一致性快照后复制 ==")
    source = Partition("orders-1", rows=_rows(120), version=5)
    result = clone(source, state_dir=state_dir)
    print(json.dumps(
        {
            "snapshot": result["snapshot"],
            "copied": result["copied"],
            "retries": result["retries"],
            "verify": result["verify"],
            "visible": result["visible"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    print("目标可见行数:", len(query("orders-1")))
    print("verify:", verify("orders-1")["matched"])

    print("\n== 2. 模拟克隆中断，自动清理并重试 ==")
    flaky = Partition(
        "orders-2", rows=_rows(120), version=2, fail_after=50, fail_times=1
    )
    result = clone(flaky, state_dir=state_dir)
    print(json.dumps(
        {
            "retries": result["retries"],
            "resumed": result["resumed"],
            "resumed_from": result["resumed_from"],
            "snapshot_version": result["snapshot"]["version"],
            "verify_ok": result["verify"]["ok"],
            "checkpoint": result["checkpoint"],
        },
        ensure_ascii=False,
        indent=2,
    ))

    print("\n== 3. 重试耗尽：清理目标、断点落盘，重启后续传 ==")
    broken = Partition(
        "orders-3", rows=_rows(120), version=3, fail_after=70, fail_times=99
    )
    try:
        clone(broken, state_dir=state_dir, max_retries=1)
    except CloneError as exc:
        print("克隆失败:", exc)
        print("目标是否可见:", query("orders-3") is not None)

    reset()  # 模拟进程重启：内存状态清空，只剩落盘快照与断点
    checkpoint = load_checkpoint("orders-3", state_dir=state_dir)
    print("重启前断点:", json.dumps(checkpoint, ensure_ascii=False))

    healthy = Partition("orders-3", rows=_rows(120), version=3)
    resumed = clone(healthy, state_dir=state_dir)
    print("重启后续传:", json.dumps(
        {
            "resumed": resumed["resumed"],
            "resumed_from": resumed["resumed_from"],
            "retries": resumed["retries"],
            "copied": resumed["copied"],
            "verify_ok": resumed["verify"]["ok"],
        },
        ensure_ascii=False,
    ))

    print("\n== 4. 校验不一致：中止并保留两侧计数 ==")
    corrupt = Partition("orders-4", rows=_rows(30), corrupt_index=7)
    try:
        clone(corrupt, state_dir=state_dir)
    except partclone.VerifyError as exc:
        print(json.dumps(
            {
                "reason": exc.result["reason"],
                "rows_source": exc.rows_source,
                "rows_target": exc.rows_target,
                "checksum_source": exc.checksum_source,
                "checksum_target": exc.checksum_target,
            },
            ensure_ascii=False,
            indent=2,
        ))
    print("目标是否可见:", query("orders-4") is not None)

    print("\n== 5. 审计记录 ==")
    for tag in ("orders-1", "orders-3", "orders-4"):
        events = [entry["event"] for entry in audit_log(tag, state_dir=state_dir)]
        print(tag, "->", events)
finally:
    reset()
    shutil.rmtree(state_dir, ignore_errors=True)

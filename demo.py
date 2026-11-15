"""tablecopy2 演示：快照 + 增量补偿 + 断点续传 + 行数/内容校验。

运行：python3 demo.py
"""

from tablecopy2 import (
    Table,
    ReadOnlyTable,
    CopyError,
    copy,
    verify,
    query,
    abort,
    snapshot_of,
    reset,
)


def show(title, result):
    print(f"--- {title} ---")
    for key in ("snapshot_position", "end_position", "caught_up", "resumed",
                "src_count", "dst_count", "matched", "status", "diff"):
        print(f"  {key}: {result[key]}")
    print()


# 1) 先在只读表上验证：快照与补偿后的行数、校验和与源表一致
print("========== 1. 只读表上验证快照复制 ==========")
reset()
ro = ReadOnlyTable("orders_ro", [{"id": 1, "amt": 10}, {"id": 2, "amt": 20}])
r1 = copy(ro, "orders_ro_copy")
show("只读表复制", r1)
print("目标表立即可查:", query("orders_ro_copy"))
print("汇总校验:", verify(), "\n")

# 2) 源表还在写：快照定版本，补偿复制期间的变更；中途失败，重试从断点继续
print("========== 2. 在线源表：故障 + 断点续传 + 增量补偿 ==========")
reset()
src = Table("orders", [{"id": i, "amt": i * 10} for i in range(1, 7)])
try:
    copy(src, "orders_copy", batch_size=2, fail_at=("bulk", 3))
except CopyError as exc:
    print("首次复制失败（故障注入）:", exc)
    print("半成品不可查，快照位点仍可查:", snapshot_of("orders_copy"), "\n")

# 复制期间源表继续写入（insert / update / delete）
src.insert({"id": 7, "amt": 70})
src.update(1, {"id": 1, "amt": 999})
src.delete(6)

r2 = copy(src, "orders_copy", batch_size=2)
show("断点续传 + 增量补偿", r2)
print("目标表内容:", query("orders_copy"))
print("与源表当前内容一致:", query("orders_copy") == src.all_rows(), "\n")

# 3) 校验不一致：停止并保留两侧数据，差异、计数、位点均可查
print("========== 3. 校验不一致：停止、保留两侧 ==========")
reset()
src2 = Table("payments", [{"id": 1, "ok": True}, {"id": 2, "ok": True}])
r3 = copy(src2, "payments_copy", tamper_after_publish=True)
show("校验拦截", r3)
print("两侧计数:", r3["src_count"], "vs", r3["dst_count"])
print("源表保留:", src2.all_rows())
print("目标表保留且可查:", query("payments_copy"))
try:
    copy(src2, "payments_copy")
except CopyError as exc:
    print("重跑被拒绝:", exc)
abort("payments_copy")
print("abort 后重新复制:", copy(src2, "payments_copy")["matched"], "\n")

print("全部演示完成。")

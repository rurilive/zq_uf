"""写租约演示：独占 / 排队 / 重入 / 幂等释放 / 过期接管。"""

import os
import tempfile
import time

from tablelease import (
    FenceRejected,
    FencedWriter,
    acquire,
    audit_log,
    configure,
    release,
    renew,
    status,
)

# demo 用独立临时库，重复运行互不干扰。
db_path = os.path.join(tempfile.mkdtemp(prefix="tablelease-demo-"), "demo.db")
configure(db_path=db_path)
print(f"租约库: {db_path}\n")

print("== 1. 独占：同一张表同时只有一个写者 ==")
first = acquire("orders", "etl-1", 30)
print("etl-1:", first)
second = acquire("orders", "etl-2", 30)
print("etl-2 排队:", {k: second[k] for k in
      ("granted", "holder", "wait_position", "expires_in")})

print("\n== 2. 重入：同一任务重复进入，引用计数 +1，不自锁 ==")
again = acquire("orders", "etl-1", 30)
print("etl-1 重入:", {k: again[k] for k in
      ("granted", "entry_count", "fence_token")})

print("\n== 3. 续约必须核对持有者 ==")
renewed = renew("orders", "etl-1", 60)
print("etl-1 续约:", renewed["expires_in"], "秒")

print("\n== 4. 释放幂等：重复释放不报错，非持有者不能抢释放 ==")
r1 = release("orders", "etl-1")
print("第一次释放（重入计数 2->1）:",
      {k: r1[k] for k in ("released", "entry_count", "holder")})
r_other = release("orders", "etl-3")
print("第三方抢释放（被拒，租约不丢）:",
      {k: r_other[k] for k in ("released", "duplicate", "holder")})
r2 = release("orders", "etl-1")
print("第二次释放（重入计数归零，真正释放）:",
      {k: r2[k] for k in ("released", "entry_count", "holder",
                          "next_waiter")})
r3 = release("orders", "etl-1")
print("第三次释放（重复，不报错）:",
      {k: r3[k] for k in ("released", "duplicate")})

print("\n== 5. 释放后立即可以重新获取 ==")
got = acquire("orders", "etl-2", 1)
print("etl-2 获取:", {k: got[k] for k in ("granted", "holder")})
release("orders", "etl-2")

print("\n== 6. 过期接管 + fencing 防覆盖 ==")
writer = FencedWriter(db_path)
a = acquire("orders", "etl-A", 1)
writer.write("row-1", "A 的数据", a["fence_token"])
print("etl-A 写入完成，等待其租约过期（模拟崩溃不释放）...")
time.sleep(1.2)
b = acquire("orders", "etl-B", 30)
print("etl-B 接管:", {k: b[k] for k in
      ("granted", "taken_over", "previous_owner", "fence_token")})
writer.write("row-1", "B 的数据", b["fence_token"])
try:
    writer.write("row-1", "A 复活的迟到写入", a["fence_token"])
except FenceRejected as exc:
    print("旧持有者迟到写入被拒:", exc)
print("最终数据:", writer.read("row-1"))

print("\n== 7. 状态可跨进程查看（只读）==")
print(status("orders"))

print("\n== 8. 接管写审计 ==")
for event in audit_log("orders"):
    if event["event"] in ("takeover", "released", "release_duplicate"):
        print(event["event"], event["owner"], event["detail"] or "")

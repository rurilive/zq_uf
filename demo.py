"""采集任务租约完整流程演示：领取、拒绝、续约、释放、FIFO、崩溃接管。"""

import threading
import time
import tempfile
import os

from crawllease import LeaseManager


def show(title, resp):
    print(f"{title:24} owner={resp['owner']!s:8} state={resp['state']:8} "
          f"剩余={resp['expires_in']:5.1f}s 接管={resp['taken_over']!s:5} "
          f"结果={resp.get('reason') or 'ok'}")


# 短 TTL + 较短检查间隔，让崩溃接管在几秒内完整演示
mgr = LeaseManager(2.0, warn_before=0.6,
                   reaper_interval=0.1,
                   on_expiring=lambda t, o, left: print(f"  ⏰ 临期提醒: {o} 的 {t} 还剩 {left:.1f}s"),
                   on_expired=lambda t, o, left: print(f"  💀 到期: {o} 的 {t} 已失效，可被接管"))

print("== 1. 领取与互斥 ==")
show("host-a 领取", mgr.acquire("task-1", "host-a"))
show("host-b 重复领取被拒", mgr.acquire("task-1", "host-b"))
show("host-a 续约", mgr.renew("task-1", "host-a"))
show("host-b 续约被拒", mgr.renew("task-1", "host-b"))

print("\n== 2. 主动释放幂等，释放后立即重领 ==")
show("host-a 释放", mgr.release("task-1", "host-a"))
show("host-a 再次释放(幂等)", mgr.release("task-1", "host-a"))
show("host-b 立即领取", mgr.acquire("task-1", "host-b"))

print("\n== 3. FIFO 等待：按到达顺序交接 ==")
order = []


def waiter(name, gate):
    gate.wait()
    r = mgr.acquire("task-1", name, wait=True, timeout=10)
    order.append(name)
    mgr.release("task-1", name)


gates = [threading.Event() for _ in range(3)]
threads = [threading.Thread(target=waiter, args=(n, g))
           for n, g in zip(("w1", "w2", "w3"), gates)]
for t in threads:
    t.start()
for g in gates:
    g.set()
    time.sleep(0.05)
print(f"  当前排队长度: {mgr.status('task-1')['queue_length']}")
mgr.release("task-1", "host-b")
for t in threads:
    t.join()
print(f"  交接顺序: {order}")

print("\n== 4. 崩溃演练：机器宕机不续约，到期后被接管 ==")
show("host-c 领取后崩溃", mgr.acquire("task-1", "host-c", ttl=1.5))
print("  ... host-c 宕机，无人续约，等待 reaper 判定到期 ...")
time.sleep(2.2)
st = mgr.status("task-1")
print(f"  到期状态: state={st['state']}, 最后持有者={st['owner']}")
show("host-d 接管", mgr.takeover("task-1", "host-d"))
show("对活跃租约接管被拒", mgr.takeover("task-1", "host-e"))

print("\n== 5. 接管记录查询与导出（可回看、可审计）==")
for rec in mgr.takeover_records("task-1"):
    print(f"  seq={rec['seq']} {rec['previous_owner']} -> {rec['owner']} "
          f"v{rec['version']} @ {rec['time']}")
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "takeovers.json")
    mgr.export_takeovers(path, task="task-1")
    print(f"  已导出: {path} ({os.path.getsize(path)} bytes)")

mgr.shutdown()
print("\n演示完成。")

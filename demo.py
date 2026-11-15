"""演示账号级登录态复用、提前续期、踢下线重登与失败冷却。

运行：``python3 demo.py``
"""

import shutil
import tempfile

import loginkeep
from loginkeep import FileStore
from loginkeep.simulator import SimulatedClock, SimulatedSite

clock = SimulatedClock()
site = SimulatedSite(ttl=600.0)
pool = loginkeep.configure(
    site=site,
    clock=clock,
    background=False,
    account_pause=120.0,
    site_pause=30.0,
    probation_successes=3,
    normal_inflight=8,
)

FIELDS = (
    "status",
    "token",
    "token_via",
    "expires_in",
    "shared",
    "refreshed",
    "relogin",
    "paused_until",
    "retry_after",
    "probation",
    "error",
    "rate",
)


def show(title, result):
    print(title)
    for key in FIELDS:
        if key in result:
            print("  %-12s %s" % (key + ":", result[key]))
    print()


def logins(account):
    return sum(1 for a in site.login_calls if a == account)


print("== 1. 同账号共享登录态 ==")
show("机器 A  lease(acc-1)", loginkeep.lease("acc-1"))
show("机器 B  lease(acc-1)", loginkeep.lease("acc-1"))
print("站点登录次数:", logins("acc-1"))
print()

print("== 2. 到期前后台提前续期 ==")
clock.advance(500)  # TTL 600，提前量 150
pool.tick()
show("后台 tick 之后 lease(acc-1)", loginkeep.lease("acc-1"))
print("续期次数:", loginkeep.status("acc-1")["refresh_count"])
print()

print("== 3. 被踢下线：只重登一次 ==")
token = loginkeep.lease("acc-1")["token"]
site.kick("acc-1")
show("refresh(旧令牌)", loginkeep.refresh(token))
print("多个并发请求拿着同一个旧令牌：")
show("  refresh(旧令牌) 第 2 次", loginkeep.refresh(token))
show("  refresh(旧令牌) 第 3 次", loginkeep.refresh(token))
print("站点登录次数:", logins("acc-1"), "（首次 + 一次重登）")
print()

print("== 4. 登录失败：区分账号问题与站点问题 ==")
site.fail_next("acc-2", kind="account")
show("acc-2 登录被拒（账号问题）", loginkeep.lease("acc-2"))
site.fail_next("acc-3", kind="site")
show("acc-3 站点不可用（站点问题）", loginkeep.lease("acc-3"))
print()

print("== 5. 暂停期间不发登录，到期自动恢复接单 ==")
before = logins("acc-2")
clock.advance(60)
pool.tick()
print("暂停中 tick 新增登录:", logins("acc-2") - before)
show("暂停中 lease(acc-2)", loginkeep.lease("acc-2"))
clock.advance(61)  # 越过 pause_until
pool.tick()
show("暂停到期后 status(acc-2)", loginkeep.status("acc-2"))
show("试探期第 2 次成功: lease(acc-2)", loginkeep.lease("acc-2"))
show("试探期第 3 次成功: lease(acc-2)", loginkeep.lease("acc-2"))
print()

print("== 6. 磁盘共享存储：多进程/多机拿到同一份登录态 ==")
shared_dir = tempfile.mkdtemp(prefix="loginkeep-demo-")
machine_a = loginkeep.configure(
    site=site,
    clock=clock,
    store=FileStore(shared_dir),
    background=False,
)
machine_b = loginkeep.SessionPool(
    site, clock=clock, store=FileStore(shared_dir), background=False
)
a = machine_a.lease("acc-9")["token"]
b = machine_b.lease("acc-9")["token"]
print("两个池拿到同一令牌:", a == b, a)
print("站点 login 次数:", logins("acc-9"))
shutil.rmtree(shared_dir, ignore_errors=True)

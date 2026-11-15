"""演示：共享登录态、踢下线单飞重登、失败暂停与自动恢复。"""
from loginkeep import ACCOUNT, LoginError, LoginPool, lease, refresh

# 模块级默认池：同账号共享同一份登录态
a = lease("acc-1")
b = lease("acc-1")
print("shared:", a["token"] == b["token"], a["shared"], "expires_in:", a["expires_in"])

# 被踢下线后只重登一次
pool = LoginPool(refresh_margin=60)
tok = pool.lease("acc-2")["token"]
pool.invalidate(tok)  # 模拟被站点踢下线
r = pool.refresh(tok)
print("relogin:", r["relogin"], "new token:", r["token"] != tok)

# 账号问题 -> 按账号暂停，返回里能区分账号/站点
def bad(account):
    raise LoginError(ACCOUNT, "密码错误")

pool2 = LoginPool(bad, pause_seconds=60)
r = pool2.lease("acc-3")
print("paused:", r["token"] is None, r["error"]["kind"], r["paused_until"] is not None)

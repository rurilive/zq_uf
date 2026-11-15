"""loginkeep 行为演示：用假时钟跑完整生命周期，无需真实网络与等待。

运行：python3 demo.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loginkeep import (
    AccountError,
    DummyBackend,
    InMemoryStore,
    KickedError,
    LoginManager,
    SiteError,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def advance(self, s):
        self.t += s


def banner(title):
    print("\n=== %s ===" % title)


def show(label, view):
    keep = {
        "state": view["state"],
        "token": view["token"],
        "expires_in": round(view["expires_in"], 1),
        "rate": view["rate"],
        "ever_refreshed": view["ever_refreshed"],
        "ever_relogin": view["ever_relogin"],
        "retry_after": round(view["retry_after"], 1),
        "error_kind": view["error_kind"],
        "pause_reason": view["pause_reason"],
    }
    print("%-28s %s" % (label, keep))


def main():
    clock = FakeClock()
    # 编排后端脚本：登录成功 -> 续期被踢 -> 重登成功 -> 账号风控 -> 站点抖动 -> 恢复
    backend = DummyBackend(
        script=[
            3600,                       # 首次登录，令牌 1 小时
            KickedError("kicked"),      # 续期时发现被踢
            3600,                       # 单飞重登成功
            AccountError("risk_ctrl"),  # 之后账号被风控
            3600, 3600, 3600,           # 试探期连续成功
            SiteError("gateway"),       # 恢复后一次站点抖动
            3600,
        ],
        clock=clock,
        default_ttl=3600,
    )
    mgr = LoginManager(
        backend=backend,
        store=InMemoryStore(),
        clock=clock,
        start_background=False,
        renew_margin=300,
        account_backoff=300,
        site_backoff=30,
        trial_success_needed=3,
    )

    # 1. 账号级复用：多次 lease 同一份登录态，只登录一次
    banner("1. 账号级复用（多机/多次请求共享同一令牌）")
    show("lease #1", mgr.lease("acc-1"))
    show("lease #2（共享）", mgr.lease("acc-1"))
    show("lease #3（共享）", mgr.lease("acc-1"))
    print("后端真实登录次数: %d（期望 1）" %
          sum(1 for c in backend.calls if c[0] == "login"))

    # 2. 踢下线：refresh 内部单飞重登一次，返回新令牌并带履历
    banner("2. 被踢下线 -> 只重登一次")
    token = mgr.store.get("acc-1").token
    show("refresh(被踢)", mgr.refresh(token))
    print("后端真实登录次数: %d（期望 2 = 首次 + 一次重登）" %
          sum(1 for c in backend.calls if c[0] == "login"))

    # 3. 账号问题 vs 站点问题：分类暂停，暂停期间不发新登录
    banner("3. 账号风控 -> 按账号暂停，暂停期间不发起登录")
    clock.advance(3601)  # 令牌过期，下一次 lease 触发登录
    show("lease(登录失败)", mgr.lease("acc-1"))
    calls_before = len(backend.calls)
    show("lease(暂停中不登录)", mgr.lease("acc-1"))
    print("暂停期间新增后端调用: %d（期望 0）" % (len(backend.calls) - calls_before))

    # 4. 暂停结束自动转 TRIAL 小流量，连续成功后恢复满速
    banner("4. 自动恢复：TRIAL 小流量 -> 连续成功 -> 满速")
    clock.advance(301)  # 跨过账号类暂停窗口
    show("lease(试探 1)", mgr.lease("acc-1"))
    clock.advance(3301)  # 令牌过期，重新登录算试探成功
    show("lease(试探 2)", mgr.lease("acc-1"))
    clock.advance(3301)
    show("lease(试探 3 -> 满速)", mgr.lease("acc-1"))

    # 5. 站点问题：短暂停，错误类型与账号问题明确区分
    banner("5. 站点抖动 -> 短暂停（与账号问题区分）")
    clock.advance(3601)
    show("lease(站点失败)", mgr.lease("acc-1"))
    clock.advance(31)
    show("lease(短暂停结束)", mgr.lease("acc-1"))

    banner("完成")
    print("后端调用序列:")
    for c in backend.calls:
        print("  ", c)


if __name__ == "__main__":
    main()

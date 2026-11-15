"""演示：使用写审计、过期失效、安全回收与状态导出。

用临时目录和手动时钟模拟时间推进，跑完自动清理。
"""

import tempfile

from sessionaudit import SessionManager


class Clock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now


def main():
    clock = Clock()
    with tempfile.TemporaryDirectory(prefix="sessionaudit-demo-") as store:
        manager = SessionManager(
            store_dir=store,
            site="demo-site",
            ttl=300,
            audit_retention=900,
            clock=clock,
        )

        print("== 使用账号（每次写审计，同账号未过期复用同一会话）==")
        print(manager.use("acc-1"))
        clock.now = 1100
        print(manager.use("acc-1"))
        print(manager.use("acc-2"))

        print("\n== acc-3 在跑任务期间到期，不能被回收 ==")
        with manager.task("acc-3"):
            clock.now = 1500
            print(manager.sweep(1500))

        print("\n== 任务结束后再 sweep，过期会话安全回收 ==")
        print(manager.sweep(1500))

        print("\n== 审计按账号查询 ==")
        for record in manager.query_audit(account="acc-1"):
            print(record)

        print("\n== 审计按站点查询 ==")
        print(f"共 {len(manager.query_audit(site='demo-site'))} 条")

        print("\n== 会话状态导出 ==")
        for session in manager.export_state()["sessions"]:
            print(session)

        manager.close()


if __name__ == "__main__":
    main()

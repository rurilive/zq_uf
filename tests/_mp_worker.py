"""跨进程单飞测试的 worker 脚本（由 test_login.py 用子进程启动）。

每个进程持有独立的 LoginManager + SqliteStore，但指向同一个 SQLite
文件，模拟两台机器。所有进程并发 lease 同一账号，最后把各自拿到的
令牌和本进程真实登录次数写到结果目录。
"""

import json
import os
import sys
import time

# 保证从任意工作目录启动都能 import 到仓库里的 loginkeep
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from loginkeep import LoginBackend, LoginManager, SqliteStore  # noqa: E402


class FileCountingBackend(LoginBackend):
    """登录计数落文件，供测试进程聚合；登录前 sleep 放大竞争窗口。"""

    def __init__(self, counter_path):
        self.counter_path = counter_path

    def login(self, account):
        # O_EXCL 原子创建实现跨进程计数
        for _ in range(100):
            try:
                fd = os.open(self.counter_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, b"1")
                os.close(fd)
                break
            except FileExistsError:
                time.sleep(0.005)
        time.sleep(0.3)  # 让其他进程有充分时间进入锁等待
        return self._result(account)

    def renew(self, account, token):  # pragma: no cover - 本测试不涉及续期
        return self._result(account, token)

    def _result(self, account, token=None):
        from loginkeep import LoginResult

        return LoginResult(
            token=token or ("tok-%s" % account),
            expires_in=3600.0,
            renewable=True,
        )


def main():
    db_path = os.environ["LK_DB_PATH"]
    counter = os.environ["LK_COUNTER_PATH"]
    result_dir = os.environ["LK_RESULT_DIR"]
    worker_id = os.environ["LK_WORKER_ID"]

    mgr = LoginManager(
        backend=FileCountingBackend(counter),
        store=SqliteStore(db_path),
        start_background=False,
        poll_interval=0.05,
        poll_max_wait=15.0,
    )
    view = mgr.lease("multi-acc")
    with open(os.path.join(result_dir, "worker-%s.json" % worker_id), "w") as f:
        json.dump({"token": view["token"], "ok": view["ok"]}, f)
    mgr.store.close()


if __name__ == "__main__":
    main()

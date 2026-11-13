"""问题复现脚本：修复前退出码为 1，修复后全部场景通过。"""

import threading
import time

from sessionkeep import EmptyResponse, LoginPageDetected, Session


def scenario_concurrent_refresh():
    barrier = threading.Barrier(8)
    logins = []

    def login():
        time.sleep(0.02)
        logins.append(1)
        return "cookie-%d" % len(logins)

    session = Session(login, lambda url, cookie: "data")
    cookies = []

    def worker():
        barrier.wait()
        cookies.append(session.ensure_session())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    ok = len(logins) == 1 and len(set(cookies)) == 1
    print("[并发刷新] 登录次数=%d 期望=1 -> %s" % (len(logins), "OK" if ok else "FAIL"))
    return ok


def scenario_login_page():
    login_page = '<html><form><input type="password" name="password"></form></html>'
    session = Session(lambda: "cookie", lambda url, cookie: login_page)
    try:
        session.fetch("http://site/list")
    except LoginPageDetected:
        ok = True
    else:
        ok = False
    print("[登录页识别] 命中登录页重登重放，仍失败时明确报错 -> %s"
          % ("OK" if ok else "FAIL"))
    return ok


def scenario_empty_page():
    session = Session(lambda: "cookie", lambda url, cookie: "   ")
    try:
        session.fetch("http://site/list")
    except EmptyResponse:
        ok = True
    else:
        ok = False
    print("[空响应] 空页面重登重放后仍为空，明确报错 -> %s"
          % ("OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    results = [
        scenario_concurrent_refresh(),
        scenario_login_page(),
        scenario_empty_page(),
    ]
    print("结论:", "全部修复" if all(results) else "问题仍存在")
    raise SystemExit(0 if all(results) else 1)

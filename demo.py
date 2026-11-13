"""两小时时间推进演示：提前刷新、登录页重放、空响应与连续失败报错。"""

import datetime as dt

from sessionkeep import EmptyResponse, Session, SessionRefreshError


class FakeClock:
    """测试/演示用时钟，advance 多少就等价于真实时间走了多少。"""

    def __init__(self):
        self.now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += dt.timedelta(minutes=minutes)


LOGIN_PAGE = '<html><form><input type="password" name="password"></form></html>'


def main():
    clock = FakeClock()
    logins = []
    serve_login_once = {"hit": True, "cookie": None}

    def login():
        cookie = "cookie-%d" % (len(logins) + 1)
        logins.append(cookie)
        # cookie 有效期 1 小时，过期时间按 UTC 计算
        return cookie, clock() + dt.timedelta(hours=1)

    def fetch_page(url, cookie):
        # cookie-2 第一次使用时服务端恰好已踢下线，返回一次登录页
        if serve_login_once["hit"] and serve_login_once["cookie"] == cookie:
            serve_login_once["hit"] = False
            return LOGIN_PAGE
        return "status=200 url=%s cookie=%s" % (url, cookie)

    # 提前 10 分钟刷新；时间全部由 FakeClock 提供，与机器时区无关
    session = Session(login, fetch_page, refresh_margin=600, time_func=clock)

    print("t=0min  ", session.fetch("http://site/list"))
    clock.advance(10)
    print("t=10min ", session.fetch("http://site/detail/1"))

    # 距 cookie-1 过期还有 10 分钟，命中提前量，单线程刷成 cookie-2
    clock.advance(40)
    session.ensure_session()
    print("t=50min 提前刷新后 cookie =", session.cookie)
    serve_login_once["cookie"] = session.cookie

    # cookie-2 首次取数就被服务端踢下线，返回登录页：
    # fetch 识别特征 -> 重登 cookie-3 -> 重放同一请求一次，调用方拿到正常数据
    print("t=70min ", session.fetch("http://site/list"))

    # 继续推进，cookie-3 进入提前刷新窗口，刷成 cookie-4
    clock.advance(70)
    session.ensure_session()
    print("t=120min 两小时后 cookie =", session.cookie)
    print("两小时内登录次数:", len(logins), logins)

    # 空响应：重登重放后仍为空，明确报错而不是把空页面入库
    empty_session = Session(lambda: "cookie", lambda url, cookie: "   ")
    try:
        empty_session.fetch("http://site/list")
    except EmptyResponse as exc:
        print("空响应报错:", exc)

    # 连续刷新失败：达到阈值抛出带原因的明确错误
    def broken_login():
        raise RuntimeError("认证服务不可用")

    broken_session = Session(
        broken_login, lambda url, cookie: "ok", max_refresh_failures=2
    )
    try:
        broken_session.ensure_session()
        broken_session.ensure_session()
    except SessionRefreshError as exc:
        print("连续刷新失败报错:", exc)


if __name__ == "__main__":
    main()

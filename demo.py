from sessionkeep import Session

LOGINS = []


def login():
    LOGINS.append(1)
    return "cookie-abc"


def fetch_page(url, cookie):
    return "status=200 url=%s cookie=%s" % (url, cookie)


session = Session(login, fetch_page)
print(session.fetch("http://site/list"))
print(session.fetch("http://site/detail/1"))
print("登录次数", len(LOGINS))

# TODO: 第二段演示（实现后补）

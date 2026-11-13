"""登录态保持的最小实现。"""


class Session:
    def __init__(self, login, fetch_page):
        self.login = login
        self.fetch_page = fetch_page
        self.cookie = None
        self.login_count = 0

    def ensure_session(self):
        if self.cookie is None:
            self.cookie = self.login()
            self.login_count += 1
        return self.cookie

    def fetch(self, url):
        cookie = self.ensure_session()
        return self.fetch_page(url, cookie)

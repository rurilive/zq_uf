"""登录验证码处理，当前遇到验证码直接失败。"""


class CaptchaRequired(Exception):
    """账号命中验证码，当前没有接管通道。"""

    def __init__(self, account, kind="image"):
        super().__init__(f"{account} 需要 {kind} 验证码")
        self.account = account
        self.kind = kind


def challenge(account):
    raise CaptchaRequired(account)


def submit(account, code):
    raise CaptchaRequired(account)

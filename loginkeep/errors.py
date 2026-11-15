"""登录/续期错误分类。

调用方需要能区分「账号本身有问题」和「站点暂时不可用」：
前者应该长暂停，后者短暂停，两者都会按账号进入冷却。
"""


class LoginError(Exception):
    """所有登录相关错误的基类。"""

    kind = "site"


class SiteError(LoginError):
    """站点侧问题：超时、5xx、网络抖动等，通常可重试。"""

    kind = "site"


class AccountError(LoginError):
    """账号侧问题：密码错误、封禁、被风控等，重试大概率仍失败。"""

    kind = "account"


class KickedError(SiteError):
    """令牌已被站点失效（被踢下线或会话过期），需要重登一次。

    这类错误不是登录失败：keeper 会改用一次新的登录来恢复。
    """

    kind = "kicked"

"""登录失败的错误分类。

站点登录适配层把底层错误翻译成这里的三类，管理器据此决定暂停策略：

- AccountError：账号本身的问题（密码错、被风控、被踢下线），
  换机器重试没有意义，按账号长暂停。
- SiteError：站点侧问题（5xx、网关超时、限流），
  账号是好的，短暂暂停后即可试探恢复。
- KickedError：登录态在有效期内被踢下线，属于账号类问题，
  单独一类以便统计和重登。
"""


class LoginError(Exception):
    """所有登录失败的基类。"""

    kind = "login_error"
    default_reason = "login_error"

    def __init__(self, reason=None):
        super().__init__(reason or self.default_reason)
        self.reason = reason or self.default_reason


class AccountError(LoginError):
    """账号问题：凭据错误、被风控、被封禁等。"""

    kind = "account"
    default_reason = "account_error"


class KickedError(AccountError):
    """登录态被站点踢下线（异地登录/单点登录冲突）。"""

    kind = "account"
    default_reason = "kicked"


class SiteError(LoginError):
    """站点问题：服务端错误、网关、超时、站点级限流。"""

    kind = "site"
    default_reason = "site_error"

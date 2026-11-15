"""登录后端抽象。

真实采集场景下由接入方实现 ``LoginBackend``：``login`` 发起一次完整登录，
``renew`` 拿现有令牌做静默续期（不重新登录、不触发单点互踢）。
适配层负责把站点返回的失败翻译成 :mod:`loginkeep.errors` 里的异常类型，
这样管理器才能区分“账号问题”与“站点问题”。

模块自带 :class:`DummyBackend` 用于演示和测试，可通过脚本控制成败序列。
"""

from dataclasses import dataclass

from .errors import AccountError, KickedError, SiteError


@dataclass
class LoginResult:
    """一次登录/续期成功后的令牌信息。

    ``token``        站点令牌（不透明字符串）
    ``expires_in``   剩余有效秒数
    ``renewable``    是否支持静默续期；不支持则只能到期重登
    """

    token: str
    expires_in: float
    renewable: bool = True


class LoginBackend:
    """接入方需要实现的登录适配接口。"""

    def login(self, account):
        """完整登录，返回 :class:`LoginResult`。

        失败时抛 :class:`~loginkeep.errors.AccountError`、
        :class:`~loginkeep.errors.SiteError` 或其子类。
        """
        raise NotImplementedError

    def renew(self, account, token):
        """用现有令牌静默续期，返回新的 :class:`LoginResult`。

        令牌被踢下线时抛 :class:`~loginkeep.errors.KickedError`，
        管理器会转为一次单飞重登。
        """
        raise NotImplementedError


class DummyBackend(LoginBackend):
    """内存假后端：不依赖网络，按预设脚本返回成功或抛错。

    脚本是一个列表，每个元素是：

    - 异常类/实例：下一次登录或续期抛出它；
    - 数字：下一次操作成功，令牌有效期为该秒数；
    - None/缺省：成功并使用默认有效期。

    登录和续期共用同一条脚本，按调用顺序逐条消费；脚本耗尽后
    所有调用都按成功处理。用它可以在测试里精确编排“先失败两次、
    再连续成功”之类的场景。
    """

    def __init__(self, script=None, default_ttl=3600, clock=None, renewable=True):
        self._script = list(script or [])
        self.default_ttl = default_ttl
        self._clock = clock
        self.renewable = renewable
        # 每个账号的登录次数，用于生成可辨认的确定性令牌
        self.login_count = {}
        # 记录调用历史，测试可断言“被踢后只重登了一次”
        self.calls = []

    def _now(self):
        return self._clock.time() if self._clock else 0

    def _pop(self):
        # 脚本耗尽后持续返回 None（按成功处理）；有元素时必须真正出队，
        # 否则 None 会被反复“消费”，预设的失败序列永远轮不到
        if self._script:
            return self._script.pop(0)
        return None

    def _resolve(self, item, exc_default):
        if item is None:
            return None
        if isinstance(item, type) and issubclass(item, Exception):
            return item()
        if isinstance(item, Exception):
            return item
        if isinstance(item, (int, float)):
            return item
        # 未知脚本元素按配置的默认异常处理
        return exc_default()

    def login(self, account):
        self.calls.append(("login", account))
        item = self._resolve(self._pop(), AccountError)
        if isinstance(item, Exception):
            raise item
        ttl = item if isinstance(item, (int, float)) else self.default_ttl
        n = self.login_count.get(account, 0) + 1
        self.login_count[account] = n
        return LoginResult(
            token="t-%s-%d" % (account, n),
            expires_in=float(ttl),
            renewable=self.renewable,
        )

    def renew(self, account, token):
        self.calls.append(("renew", account, token))
        item = self._resolve(self._pop(), SiteError)
        if isinstance(item, Exception):
            raise item
        ttl = item if isinstance(item, (int, float)) else self.default_ttl
        # 续期成功：令牌沿用，有效期重新打满
        return LoginResult(token=token, expires_in=float(ttl), renewable=True)


__all__ = ["LoginBackend", "LoginResult", "DummyBackend",
           "AccountError", "SiteError", "KickedError"]

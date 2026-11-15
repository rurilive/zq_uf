"""loginkeep：账号级登录态复用、提前续期、单飞重登与失败熔断。

对外主接口：

- ``lease(account)``：取账号的共享登录态；
- ``refresh(token)``：续期，被踢则单飞重登。

多机部署前用 :func:`configure` 注入共享存储和真实登录后端：

    from loginkeep import configure
    from loginkeep.store import SqliteStore
    configure(backend=MyBackend(), store=SqliteStore("/data/loginkeep.db"))
"""

from .backend import DummyBackend, LoginBackend, LoginResult
from .errors import AccountError, KickedError, LoginError, SiteError
from .login import lease, refresh
from .manager import (
    Clock,
    LoginManager,
    configure,
    get_manager,
    reset_default_manager,
)
from .store import (
    HEALTHY,
    PAUSED,
    TRIAL,
    InMemoryStore,
    SessionRecord,
    SqliteStore,
    open_store,
)

__all__ = [
    "lease",
    "refresh",
    "configure",
    "get_manager",
    "reset_default_manager",
    "LoginManager",
    "Clock",
    "LoginBackend",
    "LoginResult",
    "DummyBackend",
    "LoginError",
    "AccountError",
    "KickedError",
    "SiteError",
    "SessionRecord",
    "InMemoryStore",
    "SqliteStore",
    "open_store",
    "HEALTHY",
    "PAUSED",
    "TRIAL",
]

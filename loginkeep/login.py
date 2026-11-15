"""兼容入口：lease/refresh 的实现已移到 :mod:`loginkeep.manager`。

保留这个模块是为了兼容 ``from loginkeep.login import lease, refresh``
的旧调用方式，新代码直接 ``from loginkeep import lease, refresh``。
"""

from .manager import lease, refresh

__all__ = ["lease", "refresh"]

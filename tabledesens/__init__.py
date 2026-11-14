"""tabledesens：数仓字段脱敏与受控还原。

快速入口：
- mask / reveal：兼容历史签名的模块级函数（未配置引擎时为直通）；
- DesensEngine：按血缘下发策略的脱敏/还原引擎；
- Lineage：血缘图与字段规则；
- TokenVault / Keyring：版本化密钥与可吊销的还原令牌；
- AuditLog：按操作人和时间归档的审计日志。
"""

from .desens import configure, current_engine, mask, reveal
from .engine import (DesensEngine, MaskResult, RevealPermission,
                     RevealResult)
from .vault import TokenVault
from .crypto import Keyring
from .audit import AuditLog
from .policy import (DEFAULT_STRATEGY, HASH, MASK, NONE, REDACT, Lineage,
                     validate_strategy)
from .errors import (DesensError, PermissionDeniedError, PolicyError,
                     PolicyLooseningError, TokenError, TokenRevokedError)

__all__ = [
    # 兼容入口
    "mask", "reveal", "configure", "current_engine",
    # 引擎与结果
    "DesensEngine", "MaskResult", "RevealResult", "RevealPermission",
    # 策略与血缘
    "Lineage", "NONE", "MASK", "HASH", "REDACT", "DEFAULT_STRATEGY",
    "validate_strategy",
    # 密钥、令牌、审计
    "Keyring", "TokenVault", "AuditLog",
    # 异常
    "DesensError", "PolicyError", "PolicyLooseningError", "TokenError",
    "TokenRevokedError", "PermissionDeniedError",
]

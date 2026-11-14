"""tabledesens 的异常类型。"""


class DesensError(Exception):
    """所有脱敏相关异常的基类。"""


class PolicyError(DesensError):
    """策略或血缘配置非法。"""


class PolicyLooseningError(PolicyError):
    """下游节点试图把继承到的策略改松（血缘上只允许收紧）。"""


class TokenError(DesensError):
    """还原令牌缺失、非法或已失效。"""


class TokenRevokedError(TokenError):
    """令牌已被吊销，吊销立即生效。"""


class PermissionDeniedError(DesensError):
    """操作人不具备该节点的受控还原权限。"""

"""核查工具的错误类型，统一继承 ValueError，便于旧调用方捕获。"""


class ProbeError(ValueError):
    """所有核查错误的基类。"""


class UnknownHostError(ProbeError):
    """域名未登记。"""


class UnknownEndpointError(ProbeError):
    """域名下没有这个入口。"""


class ConfigError(ProbeError):
    """入口配置不合法或整改参数不合法。"""

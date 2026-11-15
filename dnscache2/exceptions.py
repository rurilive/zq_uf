"""dnscache2 统一异常。"""


class DnsCacheError(Exception):
    """所有 dnscache2 异常的基类。"""


class ResolutionError(DnsCacheError):
    """后端解析失败（无缓存兜底时抛出，有旧结果则保留旧结果并告警）。"""


class AllAddressesCoolingDownError(DnsCacheError):
    """候选地址全部处于冷却中。"""

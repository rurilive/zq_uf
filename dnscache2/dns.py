"""对外门面：保留 resolve/report 模块级 API，并开放缓存/灰度能力。

模块级函数走进程内默认缓存（线程池后台刷新、TTL 30s）。
需要注入时钟/执行器/真实 DNS 后端时，直接使用 DnsCache 与 RolloutManager。
"""

from .alerter import Alerter
from .cache import DnsCache
from .exceptions import (
    AllAddressesCoolingDownError,
    DnsCacheError,
    ResolutionError,
)
from .executor import InlineExecutor, ManualExecutor, ThreadRefreshExecutor
from .health import AddressState, HealthConfig
from .resolver import Record, StaticResolver
from .rollout import RolloutManager

# 与旧桩行为对齐：shop -> 10.0.0.1；未知站点回环地址
_DEFAULT_TABLE = {"shop": [("10.0.0.1", "idc-a")]}

_default_cache = DnsCache(StaticResolver(_DEFAULT_TABLE), ttl=30.0)


def resolve(host, force_refresh=False):
    """解析站点：返回选中地址、剩余 ttl、健康度、是否本次刷新等。"""
    return _default_cache.resolve(host, force_refresh=force_refresh)


def report(host, ip, ok, rtt):
    """上报一次采集调用结果，驱动降权/冷却/恢复。"""
    return _default_cache.report(host, ip, ok, rtt)


def invalidate(hosts=None, idc=None, sync=False):
    """机房变化时定向失效受影响站点。"""
    return _default_cache.invalidate(hosts=hosts, idc=idc, sync=sync)


def begin_switch(idc, canary_hosts):
    """机房切换灰度：只刷新灰度站点。"""
    return RolloutManager(_default_cache).begin_switch(idc, canary_hosts)


def reset_default_cache():
    """测试/进程重启场景：清空默认缓存。"""
    global _default_cache
    _default_cache.shutdown()
    _default_cache = DnsCache(StaticResolver(_DEFAULT_TABLE), ttl=30.0)


__all__ = [
    "resolve", "report", "invalidate", "begin_switch", "reset_default_cache",
    "DnsCache", "RolloutManager", "StaticResolver", "Record",
    "HealthConfig", "AddressState", "Alerter",
    "InlineExecutor", "ManualExecutor", "ThreadRefreshExecutor",
    "DnsCacheError", "ResolutionError", "AllAddressesCoolingDownError",
]

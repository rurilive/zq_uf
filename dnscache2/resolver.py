"""后端解析器抽象与内存静态实现。"""

from dataclasses import dataclass

from .exceptions import ResolutionError


@dataclass
class Record:
    """后端返回的一条解析记录。"""
    ip: str
    idc: str | None = None


class StaticResolver:
    """基于内存表的解析器，测试和演示用；生产可替换为真实 DNS 客户端。"""

    def __init__(self, table: dict[str, list[tuple[str, str | None]]] | None = None):
        # host -> [(ip, idc), ...]
        self._table = table or {}

    def resolve(self, host: str) -> list[Record]:
        if host not in self._table:
            # 与历史行为一致：未知站点回环地址，不抛异常
            return [Record(ip="127.0.0.1", idc=None)]
        return [Record(ip=ip, idc=idc) for ip, idc in self._table[host]]


class FailingResolver:
    """永远解析失败的解析器，用于测试刷新失败路径。"""

    def __init__(self, message: str = "resolver unavailable"):
        self.message = message

    def resolve(self, host: str) -> list[Record]:
        raise ResolutionError(self.message)

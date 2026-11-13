"""探测样本配置。"""

from dataclasses import dataclass, field

from .httpfetch import HttpFetcher


@dataclass
class Samples:
    """probe 的完整探测配置。

    list_urls: 列表页地址；detail_count: 每条列表提取的详情条数；
    max_workers: 并发线程数；rate_per_sec: 单站点每秒最大请求数（0 不限速）。
    """

    list_urls: list = field(default_factory=list)
    detail_count: int = 3
    max_workers: int = 4
    rate_per_sec: float = 2.0
    timeout: float = 10.0
    fetcher: object = None

    def __post_init__(self):
        if self.fetcher is None:
            self.fetcher = HttpFetcher(timeout=self.timeout)

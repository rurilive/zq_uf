"""统一计数器：重试次数、尝试次数与耗时按站点/错误类别归集。"""
from collections import defaultdict


def _new_site():
    return {
        "calls": 0,
        "successes": 0,
        "failures": 0,
        "retries": defaultdict(int),
        "attempts": defaultdict(int),
        "backoff": 0.0,
        "elapsed": 0.0,
    }


class Metrics:
    """进程内统一计数器，可通过 policy 的 ``metrics`` 键注入独立实例。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self._sites = defaultdict(_new_site)

    def record_call(self, site):
        self.calls += 1
        self._sites[site]["calls"] += 1

    def record_attempt(self, site, error_class):
        self._sites[site]["attempts"][error_class] += 1

    def record_retry(self, site, error_class, delay):
        site_data = self._sites[site]
        site_data["retries"][error_class] += 1
        site_data["backoff"] += delay

    def record_success(self, site):
        self.successes += 1
        self._sites[site]["successes"] += 1

    def record_failure(self, site):
        self.failures += 1
        self._sites[site]["failures"] += 1

    def record_elapsed(self, site, seconds):
        self._sites[site]["elapsed"] += seconds

    def snapshot(self):
        """返回可直接序列化的纯字典快照。"""
        sites = {}
        for name, data in self._sites.items():
            sites[name] = {
                "calls": data["calls"],
                "successes": data["successes"],
                "failures": data["failures"],
                "retries": dict(data["retries"]),
                "attempts": dict(data["attempts"]),
                "backoff": data["backoff"],
                "elapsed": data["elapsed"],
            }
        return {
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "sites": sites,
        }


metrics = Metrics()

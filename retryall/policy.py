"""可配置策略：默认值、按错误类别上限、按站点覆盖。"""
import copy

from .errors import ErrorClass


DEFAULT_POLICY = {
    "base_delay": 0.01,
    "factor": 2.0,
    "jitter": 0.25,
    "attempts": None,
    "site": None,
    "metrics": None,
    "classes": {
        ErrorClass.TIMEOUT: {"attempts": 3, "max_delay": 2.0},
        ErrorClass.CONNECTION: {"attempts": 3, "max_delay": 2.0},
        ErrorClass.RATE_LIMIT: {"attempts": 5, "max_delay": 30.0},
        ErrorClass.SERVER: {"attempts": 4, "max_delay": 10.0},
        ErrorClass.TRANSIENT: {"attempts": 3, "max_delay": 5.0},
    },
    "sites": {},
}


def deep_merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Policy:
    """策略对象，也可由 ``call(fn, {...})`` 直接传入字典构造。

    站点覆盖通过 ``sites`` 配置块声明，调用时以 ``site`` 键或
    :meth:`for_site` 激活，覆盖块与全局策略做深度合并。
    """

    def __init__(self, data=None, site=None):
        data = data or {}
        registry = data.get("metrics")
        merged = deep_merge(copy.deepcopy(DEFAULT_POLICY), copy.deepcopy(data))
        merged["metrics"] = registry
        self._data = merged
        self.site = site or merged.get("site")
        self.metrics = registry

    @classmethod
    def wrap(cls, policy):
        if policy is None:
            return cls()
        if isinstance(policy, Policy):
            return policy
        return cls(policy)

    def for_site(self, name):
        override = self._data["sites"].get(name, {})
        merged = deep_merge(copy.deepcopy(self._data), copy.deepcopy(override))
        merged["metrics"] = self.metrics
        cloned = Policy.__new__(Policy)
        cloned._data = merged
        cloned.site = name
        cloned.metrics = self.metrics
        return cloned

    @property
    def base_delay(self):
        return self._data["base_delay"]

    @property
    def factor(self):
        return self._data["factor"]

    @property
    def jitter(self):
        return self._data["jitter"]

    @property
    def attempts(self):
        return self._data["attempts"]

    def class_config(self, error_class):
        config = self._data["classes"].get(error_class)
        if config is None:
            config = self._data["classes"][ErrorClass.TRANSIENT]
        return config

"""冷层存储抽象。

当前为内存实现，接口保持窄而稳定，后续可替换为对象存储 / 冷库连接器，
上层 :class:`~coldhot.tier.TierManager` 不感知具体介质。
"""


class ColdTierError(Exception):
    """冷层相关错误基类。"""


class ColdTierUnavailable(ColdTierError):
    """冷层不可用（离线 / 故障 / 超时）。

    与“查无数据”严格区分：调用方据此给出提示，而不是把故障当成空结果。
    """


class ColdStorage:
    """按分片（一次归档一个分片）保存冷数据。"""

    def __init__(self):
        # 故障注入开关：置 False 后所有读写抛 ColdTierUnavailable
        self.available = True
        self._shards = {}

    def put(self, key, rows):
        if not self.available:
            raise ColdTierUnavailable(f"cold tier unavailable, cannot write {key}")
        self._shards[key] = [dict(row) for row in rows]

    def get(self, key):
        if not self.available:
            raise ColdTierUnavailable(f"cold tier unavailable, cannot read {key}")
        if key not in self._shards:
            raise KeyError(key)
        # 返回副本，避免调用方误改冷层原件
        return [dict(row) for row in self._shards[key]]

    def delete(self, key):
        if not self.available:
            raise ColdTierUnavailable(f"cold tier unavailable, cannot delete {key}")
        self._shards.pop(key, None)

    def contains(self, key):
        return key in self._shards

    # ---- 测试 / 演练用故障注入钩子 ----
    def corrupt_shard(self, key):
        """破坏分片内容，模拟冷层数据损坏（行数可能不变但内容变了）。"""
        shard = self._shards[key]
        shard[0] = dict(shard[0])
        shard[0]["_corrupted"] = True

"""站点地图分片增量合并（纯标准库）。

口径分两层：

- ``merge(old, new)`` 是 lastmod 合并原语：同 URL 比较 lastmod，大者胜出；
  结果只包含 ``new`` 覆盖到的 URL，``old`` 独有的 URL 不带入结果、也不在此
  层判删（单看一个分片无法区分“上游删除”和“属于别的分片”）。
- ``IncrementalIndex`` 在分片层判删：同一序号分片重下后消失的 URL 才确认
  删除，写墓碑；分片按序号折叠，乱序自动重排，重复应用幂等，失败保留旧索引。
"""

import time

# 墓碑默认保留 7 天；构造 IncrementalIndex 时可覆盖，None 表示永久保留。
DEFAULT_TOMBSTONE_TTL = 7 * 24 * 60 * 60

_index = {}
_diff = {"added": [], "updated": [], "deleted": [], "skipped": []}


def _merge_maps(old, new):
    """合并两个 ``url -> lastmod`` 映射。

    返回 ``(结果映射, added, updated, skipped)``：new 独有记 added，
    lastmod 更大记 updated，相同或更旧记 skipped 并保留旧值。
    """
    old = old or {}
    new = new or {}
    added, updated, skipped = [], [], []
    result = {}
    for url in sorted(new):
        lastmod = new[url]
        if url not in old:
            added.append(url)
            result[url] = lastmod
        elif lastmod > old[url]:
            updated.append(url)
            result[url] = lastmod
        else:
            skipped.append(url)
            result[url] = old[url]
    return result, added, updated, skipped


def _fold(acc, extra):
    """并集折叠：同 URL 取较大 lastmod，保留两侧所有 URL。"""
    result = dict(acc)
    for url, lastmod in (extra or {}).items():
        if url not in result or lastmod > result[url]:
            result[url] = lastmod
    return result


def merge(old, new):
    """lastmod 合并原语，结果只含 ``new`` 覆盖到的 URL。

    同 URL 按 lastmod 比较，新值覆盖旧值，相同/更旧记跳过；old 独有的
    URL 不带入结果。删除由分片层 :class:`IncrementalIndex` 判定并记墓碑。
    """
    global _index, _diff
    result, added, updated, skipped = _merge_maps(old, new)
    _index = result
    _diff = {
        "added": added,
        "updated": updated,
        "deleted": [],
        "skipped": skipped,
    }
    return {
        "total": len(result),
        "added": len(added),
        "updated": len(updated),
        "deleted": 0,
        "skipped": len(skipped),
    }


def diff():
    """返回最近一次模块级 merge 的 URL 级差异（副本）。"""
    return {key: list(values) for key, values in _diff.items()}


class IncrementalIndex:
    """分片增量索引。

    维护每个序号分片最近一次下载成功的内容、带时间戳的墓碑，以及由全部分片
    折叠（按序号排序、lastmod 取大）后再剔除墓碑 URL 得到的在线索引。任意
    增量操作后的索引都与 :meth:`rebuild` 全量重建的结果一致。
    """

    def __init__(self, tombstone_ttl=DEFAULT_TOMBSTONE_TTL):
        # 墓碑保留秒数；None 表示永久保留，只能显式清理。
        self.tombstone_ttl = tombstone_ttl
        self._shards = {}        # seq -> {url: lastmod}
        self._tombstones = {}    # url -> (lastmod, deleted_at)
        self._index = {}
        self._diff = self._empty_diff()

    @staticmethod
    def _empty_diff():
        return {"added": [], "updated": [], "deleted": [], "skipped": []}

    def _now(self):
        return time.time()

    # ---- 状态访问 -------------------------------------------------------

    @property
    def index(self):
        """当前在线索引副本（url -> lastmod，不含墓碑 URL）。"""
        return dict(self._index)

    def tombstones(self):
        """墓碑副本：url -> (删除时 lastmod, 删除时间戳)。"""
        return dict(self._tombstones)

    def shard(self, seq):
        """某序号分片最近一次下载成功的内容副本。"""
        return dict(self._shards.get(seq, {}))

    def diff(self):
        """最近一次分片操作的 URL 级差异（副本）。"""
        return {key: list(values) for key, values in self._diff.items()}

    # ---- 核心操作 -------------------------------------------------------

    def apply_shard(self, seq, urls, now=None):
        """应用一个序号分片的下载结果。

        ``urls`` 为 ``None`` 表示该分片下载失败：旧分片内容与整个索引原样
        保留，返回结果带 ``failed`` 字段，之后可直接再次调用单独重下。
        正常下载时：同分片重下后消失的 URL 记删除并写墓碑；重新出现的
        墓碑 URL 清除墓碑并记新增。
        """
        now = self._now() if now is None else now
        if urls is None:
            self._diff = self._empty_diff()
            stats = self._stats()
            stats["failed"] = seq
            return stats

        old = self._shards.get(seq, {})
        merged, added, updated, skipped = _merge_maps(old, urls)
        deleted = sorted(url for url in old if url not in urls)
        for url in deleted:
            self._tombstones[url] = (old[url], now)
        for url in urls:
            # URL 重新出现：墓碑复活（merge 原语已将其计入 added）。
            self._tombstones.pop(url, None)

        self._shards[seq] = merged
        self.cleanup(now=now)
        self._index = self._rebuild()
        self._diff = {
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "skipped": skipped,
        }
        return self._stats()

    def merge_shards(self, shards, now=None):
        """批量合并分片，接受 ``{seq: urls}`` 或 ``[(seq, urls), ...]``。

        分片顺序错乱时按序号重排后依次应用；重复分片天然幂等。返回所有
        分片聚合后的差异计数。
        """
        if isinstance(shards, dict):
            items = sorted(shards.items())
        else:
            items = sorted(shards, key=lambda item: item[0])

        aggregated = self._empty_diff()
        for seq, urls in items:
            self.apply_shard(seq, urls, now=now)
            for key in aggregated:
                aggregated[key].extend(self._diff[key])
        self._diff = {key: sorted(values) for key, values in aggregated.items()}
        return self._stats()

    def rebuild(self):
        """用当前全部分片与墓碑全量重建索引，并返回副本。"""
        self._index = self._rebuild()
        return dict(self._index)

    def cleanup(self, now=None):
        """清理过期墓碑，返回被清理的 URL 列表；ttl 为 None 时永不过期。"""
        if self.tombstone_ttl is None:
            return []
        now = self._now() if now is None else now
        expired = sorted(
            url
            for url, (_lastmod, deleted_at) in self._tombstones.items()
            if now - deleted_at >= self.tombstone_ttl
        )
        for url in expired:
            del self._tombstones[url]
        if expired:
            self._index = self._rebuild()
        return expired

    # ---- 内部 -----------------------------------------------------------

    def _rebuild(self):
        """按序号折叠全部分片（并集、lastmod 取大），再剔除存活墓碑。"""
        folded = {}
        for seq in sorted(self._shards):
            folded = _fold(folded, self._shards[seq])
        for url in self._tombstones:
            folded.pop(url, None)
        return folded

    def _stats(self):
        return {
            "total": len(self._index),
            "added": len(self._diff["added"]),
            "updated": len(self._diff["updated"]),
            "deleted": len(self._diff["deleted"]),
            "skipped": len(self._diff["skipped"]),
        }

"""模拟规则更新：缓存命中 → 规则升级 → 失效广播 → 重新编译。"""

import os
import tempfile

os.environ.setdefault("EXTRACTCACHE_HOME", tempfile.mkdtemp(prefix="extractcache-demo-"))

from extractcache import compile, invalidate, stats

rules_v1 = [
    {"id": "title", "version": "v1", "params": {"selector": "h1"}},
    {"id": "body", "version": "v1", "params": {"selector": "article"}},
]

print(compile(rules_v1))  # 首次编译（未命中，写入缓存）
print(compile(rules_v1))  # 缓存命中

# 规则更新：版本升到 v2，旧版本失效并广播给共享该缓存的其他进程
rules_v2 = [
    {"id": "title", "version": "v2", "params": {"selector": "h1.title"}},
    {"id": "body", "version": "v2", "params": {"selector": "article.main"}},
]
print(invalidate("v1"))
print(compile(rules_v2))  # 新版本重新编译
print(compile(rules_v2))  # 再次命中

print(stats())  # 命中、失效、淘汰和按规则统计的命中率

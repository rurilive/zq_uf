"""fieldpatch：按字段策略的采集结果修补。

- :func:`patch` 单行无状态修补（必填隔离 / 可空补默认 / 枚举降级），留字段来源与原值
- :func:`run_batch` 批量修补，按批次留痕，支持试运行(dry_run)与缺失率告警
- :func:`report` 按天 + 站点汇总修补、隔离、来源与缺失比例
- :func:`rollback` / :func:`replay` 按批次回滚与回放
- :func:`export_patches` 按天/站点/批次导出留痕（dict / csv）
"""

from .patch import patch
from .store import Store

_default_store = Store()


def run_batch(rows, schema, site=None, day=None, dry_run=False, store=None):
    return (store or _default_store).run_batch(
        rows, schema, site=site, day=day, dry_run=dry_run
    )


def report(day=None, site=None, store=None):
    return (store or _default_store).report(day=day, site=site)


def rollback(batch_id, store=None):
    return (store or _default_store).rollback(batch_id)


def replay(batch_id, store=None):
    return (store or _default_store).replay(batch_id)


def export_patches(day=None, site=None, batch_id=None, fmt="dict", store=None):
    return (store or _default_store).export_patches(
        day=day, site=site, batch_id=batch_id, fmt=fmt
    )


def reset(store=None):
    (store or _default_store).reset()


__all__ = [
    "patch",
    "run_batch",
    "report",
    "rollback",
    "replay",
    "export_patches",
    "reset",
    "Store",
]

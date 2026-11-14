"""站点策略，当前单层覆盖。"""

DEFAULT = {"rate": 1, "retry": 3, "timeout": 30}


def resolve(site, site_cfg=None):
    """站点配置直接覆盖默认值。"""
    merged = dict(DEFAULT)
    merged.update(site_cfg or {})
    return merged


def update(patch):
    """全量替换，没有快照和审计。"""
    DEFAULT.update(patch)
    return dict(DEFAULT)

"""表结构迁移，当前只支持向前。"""

_version = {"value": 1}


def current():
    return _version["value"]


def upgrade(version):
    """直接改版本号，没有校验和回退。"""
    _version["value"] = version
    return {"version": version, "steps": [version]}


def downgrade(version):
    raise NotImplementedError("downgrade not supported")

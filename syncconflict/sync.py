"""两端数据同步，当前后到直接覆盖。"""


def merge(local, remote):
    """远端字段覆盖本地，没有版本和墓碑。"""
    row = dict(local)
    row.update(remote)
    return row

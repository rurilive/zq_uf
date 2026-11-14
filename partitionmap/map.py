"""分区路由，当前固定前缀。"""


def route(dataset, partition):
    """一个数据集一个路径。"""
    return "%s/%s" % (dataset, partition)


def migrate(plan):
    """没有迁移动作。"""
    return {"moved": 0}

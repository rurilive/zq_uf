"""分片双活，当前后选的直接生效。"""

_owners = {}


def elect(shard, node):
    """后选节点覆盖前一个，没有任期。"""
    _owners[shard] = node
    return {"shard": shard, "node": node, "term": 0}


def write(shard, node, record):
    """任何节点都能写。"""
    return {"shard": shard, "node": node, "written": True}

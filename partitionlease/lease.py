"""分区租约，当前后到覆盖。"""

_leases = {}


def acquire(dataset, partition, owner, ttl):
    """没有过期和栅栏。"""
    _leases[(dataset, partition)] = owner
    return {"dataset": dataset, "partition": partition, "owner": owner}


def renew(dataset, partition, owner, ttl):
    return _leases.get((dataset, partition)) == owner


def release(dataset, partition, owner):
    _leases.pop((dataset, partition), None)
    return True

"""采集出口计费，当前所有请求都计费。"""

_balance = {}


def charge(proxy, amount):
    """成功和失败都累加到同一个余额。"""
    _balance[proxy] = _balance.get(proxy, 0.0) + float(amount)
    return {"proxy": proxy, "amount": amount, "balance": _balance[proxy]}


def settle(proxy):
    """只返回余额，没有退费和差异。"""
    return {"proxy": proxy, "balance": _balance.get(proxy, 0.0), "diff": 0.0, "entries": []}

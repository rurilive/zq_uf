"""站点能力记忆，当前每次重新探测。"""

_memo = {}


def remember(host, profile):
    """把探测结果写进进程内字典，但没有读取路径。"""
    _memo[host] = dict(profile)
    return {"host": host, "stored": True}


def get(host):
    """每次都返回一份新的默认画像，不读记忆。"""
    return {
        "host": host,
        "profile": {"headers": {}, "encoding": "utf-8"},
        "cached": False,
    }

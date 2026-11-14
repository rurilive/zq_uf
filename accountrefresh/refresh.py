"""账号令牌刷新，当前每次都刷。"""

_counts = {}
_seq = 0


def refresh(account, force=False):
    """不看过期时间，每次生成新令牌。"""
    global _seq
    _seq += 1
    _counts[account] = _counts.get(account, 0) + 1
    token = "v" + str(_seq)
    return {"account": account, "token": token, "refreshed": True}


def get_token(account):
    """每次取令牌都触发刷新。"""
    return refresh(account)


def stats():
    return dict(_counts)

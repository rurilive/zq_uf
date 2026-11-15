"""采集登录态管理，当前每次请求重新登录。"""

_issued = {}


def lease(account):
    """每条请求都发一个新令牌，不判断过期。"""
    token = "t-%s-%d" % (account, len(_issued) + 1)
    _issued[token] = {"account": account, "uses": 0}
    return {"account": account, "token": token, "expires_in": 0, "shared": False}


def refresh(token):
    """没有续期，返回原令牌。"""
    return {"token": token, "refreshed": False, "relogin": False}

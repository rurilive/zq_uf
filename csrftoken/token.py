"""表单令牌，当前每次重新抓。"""

_tokens = {}
_seq = 0


def get(page, step):
    """每次生成新令牌，不看作用域。"""
    global _seq
    _seq += 1
    token = "t%d" % _seq
    _tokens[page] = token
    return {"page": page, "step": step, "token": token}


def invalidate(page):
    _tokens.pop(page, None)
    return True

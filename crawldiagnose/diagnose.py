"""采集断点记录，当前只存方法和URL，回放原样返回。"""

_captures = {}


def capture(request):
    """存一条断点，头和体都不保留。"""
    cid = str(len(_captures) + 1)
    _captures[cid] = {
        "id": cid,
        "method": request.get("method"),
        "url": request.get("url"),
        "status": request.get("status"),
        "response": request.get("response"),
    }
    return cid


def diagnose(cid):
    """读回断点，不做重放也不做对比。"""
    if cid not in _captures:
        raise KeyError(cid)
    return dict(_captures[cid])

"""会话出口粘性，当前全局一份。"""

_bindings = {}


def bind(session, site):
    """直接覆盖，没有过期。"""
    _bindings[session] = {"site": site, "proxy": "10.0.0.1:80"}
    return dict(_bindings[session])


def rebind(session, reason):
    """重绑只换出口，不记原因。"""
    _bindings.setdefault(session, {})["proxy"] = "10.0.0.2:80"
    return dict(_bindings.get(session) or {})

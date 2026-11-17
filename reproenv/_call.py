"""按位置参数自适应调用外部注入的 callable。

resolver / runner 由调用方注入，签名可能是 ``(spec, report)`` 也可能是
``(spec,)``。这里按签名裁掉多余的位置参数，避免因为多传一个参数就
把自定义实现打挂。
"""

import inspect


def _shape(fn):
    """返回 ``(是否 *args, 最大位置参数数, 必填位置参数数)``；无签名返回 None。"""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    maximum = 0
    required = 0
    varargs = False
    for parameter in signature.parameters.values():
        if parameter.kind is parameter.VAR_POSITIONAL:
            varargs = True
        elif parameter.kind in (
            parameter.POSITIONAL_ONLY,
            parameter.POSITIONAL_OR_KEYWORD,
        ):
            maximum += 1
            if parameter.default is parameter.empty:
                required += 1
    return varargs, maximum, required


def invoke(fn, args):
    """调用 ``fn``，多给的参数按签名裁掉。"""
    shape = _shape(fn)
    if shape is None:
        # 拿不到签名（某些内建 callable）：先按完整参数调用，再退回单个参数。
        try:
            return fn(*args)
        except TypeError:
            if len(args) > 1:
                return fn(args[0])
            raise
    varargs, maximum, required = shape
    if varargs or maximum >= len(args):
        return fn(*args)
    count = max(required, min(len(args), maximum))
    return fn(*args[:count])

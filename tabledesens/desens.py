"""模块级兼容入口。

历史行为（无引擎时保持不变，旧调用方与既有测试不受影响）：
- ``mask(row)`` 返回行的副本，不做脱敏；
- ``reveal(row, token)`` 返回行的副本，不校验令牌。

新接入方应显式构造 :class:`~tabledesens.engine.DesensEngine`，或通过
:func:`configure` 注入一个全局引擎；注入后模块级函数即转发到引擎，
``mask`` 返回 :class:`~tabledesens.engine.MaskResult`，``reveal`` 返回
:class:`~tabledesens.engine.RevealResult`。
"""

_engine = None


def configure(engine):
    """注入全局脱敏引擎；传 None 可恢复到历史直通行为。"""
    global _engine
    _engine = engine


def current_engine():
    """返回当前全局引擎，未配置时为 None。"""
    return _engine


def mask(row):
    """脱敏一行。未配置引擎时保持历史行为：原样返回行副本。"""
    if _engine is None:
        return dict(row)
    return _engine.mask(row)


def reveal(row, token):
    """受控还原。未配置引擎时保持历史行为：原样返回行副本。"""
    if _engine is None:
        return dict(row)
    return _engine.reveal(row, token)

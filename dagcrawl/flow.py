"""采集流程，当前步骤串在一个列表里顺序执行。"""


def run(flow, context):
    """任何一步抛错整条停止，context 原地修改。"""
    for step in flow["steps"]:
        context = step(context)
        if context is None:
            raise RuntimeError("step returned None")
    return context

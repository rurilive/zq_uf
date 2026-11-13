"""顺序执行步骤流的最小实现。"""


def run(flow, context):
    ctx = dict(context)
    for step in flow:
        result = step(ctx) or {}
        ctx.update(result)
    return ctx

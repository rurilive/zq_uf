"""按顺序执行登录步骤。"""


def run(account, steps):
    context = {"account": account}
    for step in steps:
        context.update(step(context) or {})
    return context

"""演示：报销审批步骤流，包含失败、错误定位、断点续跑与条件分支。

运行：python3 demo.py
"""

from flowstep import Branch, Step, dumps, fail, registry, run


@registry.register
def open_form(ctx):
    print("  执行 open_form（幂等，每次重跑都会刷新）")
    return {"form": "报销单#%s" % ctx["user"]}


@registry.register
def fill_form(ctx):
    print("  执行 fill_form（非幂等，写操作，只执行一次）")
    return {"form_id": "F-1001"}


@registry.register
def check_budget(ctx):
    print("  执行 check_budget（幂等校验）")
    if ctx["amount"] > ctx["budget_limit"]:
        fail("BUDGET_EXCEEDED", field="amount",
             message="报销金额 %.2f 超过预算 %.2f"
                     % (ctx["amount"], ctx["budget_limit"]))
    return {"budget_ok": True}


@registry.register
def manager_approve(ctx):
    print("  执行 manager_approve（金额 > 1000 才走该分支）")
    return {"approved_by": "经理"}


@registry.register
def auto_approve(ctx):
    print("  执行 auto_approve（小额自动审批分支）")
    return {"approved_by": "系统"}


@registry.register
def send_notice(ctx):
    print("  执行 send_notice（非幂等通知，只发一次）")
    return {"notice": "已通知 %s" % ctx["user"]}


flow = [
    Step("open_form", idempotent=True, outputs=["form"]),
    Step("fill_form", idempotent=False, inputs=["form"], outputs=["form_id"]),
    Step("check_budget", idempotent=True,
         inputs=["amount", "budget_limit"], outputs=["budget_ok"]),
    Branch([
        ({"field": "amount", "op": "gt", "value": 1000},
         [Step("manager_approve", idempotent=True, outputs=["approved_by"])]),
        (None,
         [Step("auto_approve", idempotent=True, outputs=["approved_by"])]),
    ]),
    Step("send_notice", idempotent=False, outputs=["notice"]),
]

print("== 流程定义可序列化为 JSON ==")
print(dumps(flow))

print("\n== 第一次运行：预算校验失败 ==")
first = run(flow, {"user": "张三", "amount": 2500, "budget_limit": 2000})
print("ok =", first.ok)
print("错误定位:", first.error)
print("已完成步骤（断点）:", first.completed_steps)

print("\n== 第二次运行：带上检查点从失败步骤继续 ==")
fixed_context = dict(first)
fixed_context["budget_limit"] = 3000
second = run(flow, fixed_context)
print("ok =", second.ok)
print("审批分支结果:", second["approved_by"])
print("最终上下文:", {k: v for k, v in second.items() if k != "__flowstep__"})
print("检查点:", second.flow_state)

print("\n== 第三次运行：全部已完成，非幂等步骤全部跳过 ==")
third = run(flow, second)
print("ok =", third.ok, "| 通知仍为:", third["notice"])

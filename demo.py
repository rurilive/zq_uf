from retrybudget import BudgetExhausted, attempt, with_budget


def call(task, name):
    """单层调用：预算够就按层重试，耗尽立即放弃并向上抛。"""
    for _ in range(3):
        result = attempt(task)
        print(f"{name}: attempt={result['attempt']} retry={result['retry']}")
        if not result["retry"]:
            return


# 三层调用共享同一条链路的预算：L0 -> L1 -> L2
root = with_budget({"id": "req-1"}, 4)
try:
    call(root, "L0")
    child = with_budget({"id": "req-1/child"}, root)
    call(child, "L1")
    grandchild = with_budget({"id": "req-1/grandchild"}, child)
    call(grandchild, "L2")
except BudgetExhausted as exc:
    print(f"放弃于第 {exc.layer} 层，放大倍数 {exc.amplification}x，不可重试")

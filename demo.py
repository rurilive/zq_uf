from retrybudget import attempt, budget_report, with_budget


def call(task, downstream):
    """当前层在预算内重试，随后把剩余预算传给下一层。"""
    while True:
        result = attempt(task)
        print(f"{task['layer']}: attempt={result['attempt']} retry={result['retry']}")
        if result.get("exhausted"):
            print(f"{task['layer']}: {result['error']}")
            return
        if not result["retry"]:
            break
    if downstream:
        child = with_budget(downstream[0], task)
        print(f"{task['layer']} -> {child['layer']}: 剩余预算 {child['budget']}")
        call(child, downstream[1:])


def main():
    root = with_budget({"id": "req-1", "layer": "L1-ingress"}, 4)
    call(root, [
        {"id": "req-1", "layer": "L2-fetch"},
        {"id": "req-1", "layer": "L3-parse"},
    ])
    stats = budget_report(root)
    print(f"放大倍数={stats['amplification']} 放弃层级={stats['abandoned_at']} "
          f"剩余预算={stats['budget_remaining']}")


if __name__ == "__main__":
    main()

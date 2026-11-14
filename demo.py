from retrybudget import attempt, with_budget

task = with_budget({"id": "req-1"}, 3)
for _ in range(3):
    print(attempt(task))

# TODO: 第二段演示（实现后补）

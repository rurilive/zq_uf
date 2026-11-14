"""模拟预算不足：高优大任务降级、低优任务耗尽失败、断点恢复，全程审计。"""

from jobbudget2 import BudgetManager


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def main():
    clock = FakeClock()
    mgr = BudgetManager(clock=clock, history={"etl": [100.0, 140.0]})

    # 1. 按类型/历史/优先级分配预算
    big = mgr.start({"name": "big-etl", "type": "etl", "priority": "high"})
    small = mgr.start({"name": "small-sync", "priority": "low"})
    print("预算:", big["job"], big["budget"], "|", small["job"], small["budget"])

    # 2. 时间推进，进度落后 -> 降级并记录原因
    clock.now += 100.0
    print("降级:", mgr.checkpoint("big-etl", {"done": 3, "total": 10}))

    # 3. 预算耗尽 -> 提前失败，进度已保存
    clock.now += 200.0
    print("失败:", mgr.checkpoint("big-etl", {"done": 4, "total": 10}))

    # 4. 从断点恢复，恢复结果按任务输出
    print("恢复:", mgr.start({"name": "big-etl", "type": "etl", "priority": "high"}))

    # 5. 审计：预算、降级、失败、恢复
    print("审计:")
    for record in mgr.audit:
        print(" ", record)


if __name__ == "__main__":
    main()

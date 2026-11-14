"""演示：上游失败向下游传播、跳过低成本标记、修复后局部重跑。

运行：python3 demo.py
状态文件落在 ./.jobdep/<run_id>/state.json。
"""

from jobdep import FAILED, SKIPPED, DONE, rerun, run

# extract 第一次抛错，修复（置回 True）后重跑即可成功。
flaky = {"broken": True}


def extract(state):
    if flaky["broken"]:
        raise RuntimeError("extract 上游失败：数据源不可用")
    print("  extract 产出数据")


def transform(state):
    print("  transform 加工数据")


def load(state):
    print("  load 写入结果表")


dag = {
    "steps": [
        {"name": "extract", "fn": extract},
        {"name": "transform", "fn": transform, "deps": ["extract"]},
        {"name": "load", "fn": load, "deps": ["transform"]},
    ]
}

print("== 首次运行：extract 失败，下游应全部跳过，不产出空表 ==")
try:
    run(dag)
except RuntimeError as exc:
    first = exc.state  # 失败时异常对象带着完整状态
    print("  抛出:", exc)
    print("  节点状态:", dict(first))
    print("  跳过原因:",
          {n: first.reason(n) for n in first.skipped_nodes()})
    print("  落盘状态:", first.state_path)

print("\n== 修复上游后局部重跑：只跑 failed + skipped，成功节点复用 ==")
flaky["broken"] = False
second = rerun(first)
print("  重跑后状态:", dict(second))
print("  failed:", second.failed_nodes(),
      "skipped:", second.skipped_nodes(),
      "done:", second.done_nodes())
print("  第 %d 次重跑，状态已回写: %s" % (second.reruns, second.state_path))
assert all(second[n] == DONE for n in ("extract", "transform", "load"))
print("\nOK：失败不再产出空表，重跑只覆盖失败与跳过节点。")

"""增量画像演示。

第一段：同一个字段，全量重算 vs 按批增量，对比误差。
第二段：连续采集、类型变化、异常样本复核、版本回放与导出。
"""

import json
import random

from profiler2 import Profiler


def part_one():
    random.seed(2024)
    rows = [{"v": random.lognormvariate(4.0, 1.2)} for _ in range(20000)]

    profiler = Profiler(autosave=False)
    for start in range(0, len(rows), 1000):
        profiler.observe(rows[start:start + 1000])

    report = profiler.verify("v", rows, tolerance=0.01)
    print("== 全量 vs 增量 ==")
    print("tolerance:", report["tolerance"],
          "within:", report["within_tolerance"])
    for name in ("count", "mean", "p50", "p99"):
        diff = report["differences"][name]
        error = "-" if diff["error"] is None else "%.2e" % diff["error"]
        print("%-6s 增量=%-12.4f 全量=%-12.4f 相对误差=%s"
              % (name, diff["incremental"], diff["full"], error))
    profiler.close()


def part_two():
    profiler = Profiler(path="/tmp/profiler2-demo.json", autosave=True)

    profiler.observe([{"amount": float(i % 100)} for i in range(500)])
    profiler.observe([{"amount": 250000.0}, {"amount": None}])  # 一条空值、一条长尾
    profiler.flush(5)

    print("\n== 画像 ==")
    snapshot = profiler.profile("amount")
    profile = snapshot["profile"]
    print("version:", snapshot["version"], "updated_at:", snapshot["updated_at"])
    print("count:", profile["count"], "null_ratio: %.4f" % profile["null_ratio"],
          "mean: %.2f" % profile["numeric"]["mean"])
    print("quantiles:", json.dumps(profile["numeric"]["quantiles"]))

    print("\n== 异常样本 ==")
    samples = profiler.outliers("amount", only_pending=True)
    for sample in samples:
        print("value=%s upper=%s" % (sample["value"], round(sample["upper"], 2)))
        profiler.review("amount", sample["id"], "false_positive",
                        note="上游单位错误", reviewer="oncall")
    print("待复核:", len(profiler.outliers("amount", only_pending=True)))

    print("\n== 类型变化后重置 ==")
    profiler.observe([{"amount": "N/A"} for _ in range(10)])
    profiler.flush(5)
    reset = profiler.profile("amount")["profile"]
    print("category:", reset["category"], "count:", reset["count"],
          "resets:", reset["resets"])

    print("\n== 版本历史与回放 ==")
    print("versions:", [h["version"] for h in profiler.history("amount")])
    restored = profiler.replay(1)
    print("replay -> version:", restored["version"],
          "category:", restored["fields"]["amount"]["profile"]["category"])

    print("\n== 导出（字段名与版本）==")
    exported = json.loads(profiler.export())
    print("version:", exported["version"], "fields:", list(exported["fields"]))
    profiler.close()


if __name__ == "__main__":
    part_one()
    part_two()

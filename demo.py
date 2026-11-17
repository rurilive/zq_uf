"""端到端演示：同一条报告多次复现、失败链路、单步重跑与清理。"""

import json
import os
import shutil
import tempfile

from reproenv import build, cleanup, compare, history, reset, verify

BASE = tempfile.mkdtemp(prefix="reproenv-demo-")
reset(remove_dirs=True)

REPORT = {
    "id": "r-1",
    "version": "1.4.2",
    "deps": ["requests==2.31.0"],
    "initial_data": {"seed.txt": "初始数据"},
    "steps": [{"name": "启动服务", "expect": "listening"}, {"name": "发送请求", "expect": "200"}],
}

# 第一段：同一条报告拉起两个隔离环境，复现结论应一致
first = build(REPORT, base_dir=BASE)
second = build(REPORT, base_dir=BASE)
print("环境标识", first["env_id"], second["env_id"])
print("目标版本", first["version"])
print("工作目录隔离", first["workspace"] != second["workspace"])
out1, out2 = verify(first), verify(second)
print("验证结果", out1["ok"], out2["ok"], "步骤数", len(out1["results"]))
print("多次复现一致性", compare(first, second)["consistent"])
print("同报告历史", [row["env_id"] for row in history("r-1")])

# 第二段：从报告文件直接拉起；依赖拉取失败停在准备阶段，verify 不跑任何步骤
report_path = os.path.join(BASE, "report.json")
with open(report_path, "w", encoding="utf-8") as fh:
    json.dump(dict(REPORT, id="r-2"), fh, ensure_ascii=False)
print("\n从报告文件拉起", build(report_path, base_dir=BASE)["version"])

bad = build(dict(REPORT, deps=["requests==2.31.0", "missing-lib==9.9.9"]), base_dir=BASE)
print("失败环境状态", bad["state"])
print("准备阶段日志", bad["logs"][-1])
blocked = verify(bad)
print("verify 被拦下", blocked["ok"], "执行步骤数", len(blocked["results"]))

# 第三段：验证失败定位卡点，修正后单独重跑该步骤
flaky = build(
    {
        "id": "r-3",
        "version": "1.4.2",
        "deps": ["requests==2.31.0"],
        "steps": [
            {"name": "启动服务", "expect": "listening"},
            {"name": "发送请求", "expect": "200", "actual": "500"},
        ],
    },
    base_dir=BASE,
)
failed = verify(flaky)
print("\n卡在步骤", failed["failed_step"], "详情", failed["results"][-1]["detail"])
flaky["steps"][1]["actual"] = "200"
rerun = verify(flaky, step="发送请求")
print("单步重跑", rerun["rerun"], rerun["results"][0]["ok"])
print("完整复跑", verify(flaky)["ok"])

# 第四段：资源超预期提前终止
capped = build(dict(REPORT, id="r-4"), base_dir=BASE, limits={"disk_bytes": 50_000})
print("\n资源超限状态", capped["state"], capped["reason"])

# 用完清理：目录真删，且不影响别的环境
print("\n清理状态", cleanup(first)["cleanup"], "目录存在?", os.path.exists(first["workspace"]))
print("另一环境仍在", os.path.isdir(second["workspace"]))
reset(remove_dirs=True)
shutil.rmtree(BASE, ignore_errors=True)

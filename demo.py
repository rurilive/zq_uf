import tempfile

from reproenv import build, cleanup, compare, configure, rerun_step, verify

# 所有工作区放进一个临时根目录，跑完随进程清理。
configure(root=tempfile.mkdtemp(prefix="reproenv-demo-"))

REPORT = {
    "id": "r-1",
    "version": "1.4.2",
    "deps": ["requests==2.31.0"],
    "data": {"seed": "admin:123456"},
    "steps": [{"name": "启动服务", "expect": "listening"},
              {"name": "发送请求", "expect": "200"}],
}

# 第一段：拉起 -> 验证 -> 清理，返回环境标识、目标版本、步骤结果和清理状态。
env = build(REPORT)
print("环境标识", env["env_id"])
print("目标版本", env["version"])
out = verify(env)
print("验证结果", out["ok"], "步骤数", len(out["results"]),
      "卡在哪步", out["failed_step"])
print("清理状态", cleanup(env)["removed"])
print("-" * 40)

# 第二段：同一条报告拉起两个独立环境，结果放在一起比对，应完全一致。
env_a = build(REPORT)
env_b = build(REPORT)
print("工作区隔离", env_a["workspace"] != env_b["workspace"])
cmp = compare(verify(env_a), verify(env_b))
print("两次复现一致", cmp["consistent"])

# 验证步骤可以单独重跑。
print("单步重跑", rerun_step(env_a, "发送请求"))

# 依赖拉取失败：停在准备阶段，不进入验证，日志保留失败包名和版本。
bad = build(dict(REPORT, fail_deps=["requests==2.31.0"]))
print("依赖失败状态", bad["state"], "失败依赖", bad["prepare_error"]["dep"])
try:
    verify(bad)
except RuntimeError as exc:
    print("验证被拦截:", exc)

# 占用资源超预期：提前终止并标明原因。
heavy = build(dict(REPORT, resource_limit={"max_steps": 1}))
aborted = verify(heavy)
print("资源限制", aborted["ok"], aborted["reason"])

for item in (env_a, env_b, bad, heavy):
    cleanup(item)
print("全部清理完成")

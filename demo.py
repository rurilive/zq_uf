from reproenv import build, cleanup, verify

REPORT = {
    "id": "r-1",
    "version": "1.4.2",
    "deps": ["requests==2.31.0"],
    "steps": [{"name": "启动服务", "expect": "listening"}, {"name": "发送请求", "expect": "200"}],
}

env = build(REPORT)
print("环境标识", env["env_id"])
print("目标版本", env["version"])
out = verify(env)
print("验证结果", out["ok"], "步骤数", len(out["results"]))
print("清理状态", cleanup(env)["removed"])

# TODO: 第二段演示（实现后补）

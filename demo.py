"""演示：组合判定、跨宿主一致性、主机合并与误报标记。"""
import json

from containerexit import check, mark_false_positive, report, reset


def show(title, value):
    print(f"\n===== {title} =====")
    print(json.dumps(value, ensure_ascii=False, indent=2))


reset()

# 1) 旧逻辑的痛点对照：特权单项不再一律高危；真正的逃逸组合被识别
show("特权但无逃逸面 -> medium", check({
    "name": "priv-only", "host": "h1", "privileged": True,
}))
show("特权 + 可写宿主目录 -> high（H02）", check({
    "name": "priv-mount", "host": "h1", "privileged": True,
    "mounts": ["/etc:/host/etc"],
}))
show("非特权 + SYS_ADMIN + docker.sock -> high（H06）", check({
    "name": "sock", "host": "h1",
    "caps": ["SYS_ADMIN"],
    "mounts": ["/var/run/docker.sock:/var/run/docker.sock"],
}))

# 2) 同一配置部署在两台宿主上：指纹与结论一致，只是宿主不同
same_cfg = {
    "image": "app:1.2",
    "privileged": True,
    "mounts": ["/var/lib/app/data:/data"],
}
check({**same_cfg, "name": "app-a", "host": "node-7"})
check({**same_cfg, "name": "app-b", "host": "node-9"})

# 3) 同主机多容器 + 误报标记（带实际配置）
check({"name": "clean", "host": "node-7"})
check({
    "name": "ops", "host": "h1",
    "mounts": ["/var/run/docker.sock:/var/run/docker.sock"],
})  # 单项 socket -> medium（M01）
mark_false_positive(
    "h1", "ops",
    reason="socket 经网络策略仅允许访问受控代理，业务侧已确认无容器创建权限",
    actual_config={"socket_access": "restricted-proxy-only"},
)

rep = report()
show("主机合并视图（容器可展开）", rep["host_groups"])
show("跨宿主同配置对比", rep["config_groups"])
show("误报标记", rep["marked"])
show("汇总", {k: rep[k] for k in
              ("containers", "hosts", "high", "medium", "low", "none", "false_positives")})

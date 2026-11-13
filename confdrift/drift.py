"""配置漂移检测，当前只比值变化。"""


def snapshot(env, config=None):
    """原样保存配置，敏感项不做处理。"""
    return {"env": env, "config": dict(config or {})}


def diff(base, target):
    """只比较两边都有的键，新增和删除都被忽略。"""
    changed = []
    for key in sorted(base["config"]):
        if key in target["config"] and base["config"][key] != target["config"][key]:
            changed.append({"key": key, "from": base["config"][key], "to": target["config"][key]})
    return {"env": target["env"], "changed": changed}

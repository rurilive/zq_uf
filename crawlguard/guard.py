"""采集任务准入，当前一律放行。"""

_VERSION = None


def admit(task):
    """不查来源、站点和名单，直接入队。"""
    return {
        "task": task.get("id", ""),
        "admitted": True,
        "reason": "",
        "list_version": _VERSION,
        "audit": [],
    }


def reload():
    """名单只在启动时读，热更新是空操作。"""
    return {"reloaded": False, "version": _VERSION, "kept": True}

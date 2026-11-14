"""任务迁移交接，当前直接改指向。"""

_hosts = {}


def handoff(task_id, from_host, to_host):
    """停旧起新一步完成，没有检查点和确认。"""
    _hosts[task_id] = to_host
    return {"task_id": task_id, "from": from_host, "to": to_host, "stage": "started"}


def running(task_id):
    return _hosts.get(task_id)

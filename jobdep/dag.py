"""任务依赖，当前线性执行。"""


def run(dag):
    """按列表顺序跑，失败即停。"""
    state = {}
    for step in dag.get("steps") or []:
        try:
            step["fn"](state)
        except Exception as exc:
            state[step["name"]] = "failed"
            raise
        state[step["name"]] = "done"
    return state

"""演练阶段的登记与查看，当前只按登记顺序平铺。"""

_exercises = {}


def plan(exercise):
    if not isinstance(exercise, dict):
        raise ValueError("exercise 需要字典")
    name = exercise.get("name")
    if not name:
        raise ValueError("缺少演练名称")
    stages = []
    for item in exercise.get("stages", []):
        stages.append(
            {
                "name": item.get("name"),
                "goal": item.get("goal"),
                "owner": None,
                "after": list(item.get("after", [])),
                "status": "pending",
                "started": None,
                "result": None,
            }
        )
    _exercises[name] = {"stages": stages, "halted": False, "assets": []}
    return {
        "exercise": name,
        "stages": stages,
        "halted": False,
        "assets": [],
        "recovered": [],
    }


def start(exercise, stage):
    record = _exercises.get(exercise)
    if record is None:
        raise ValueError("未知演练")
    for item in record["stages"]:
        if item["name"] == stage:
            item["status"] = "running"
            return {"stage": stage, "status": "running", "started": None, "blocked_by": []}
    raise ValueError("未知阶段")


def halt(exercise, reason):
    record = _exercises.get(exercise)
    if record is None:
        raise ValueError("未知演练")
    record["halted"] = True
    for item in record["stages"]:
        if item["status"] == "running":
            item["status"] = "stopped"
    return {
        "exercise": exercise,
        "halted": True,
        "reason": reason,
        "assets": [],
        "recovered": [],
    }


def timeline(exercise):
    record = _exercises.get(exercise)
    if record is None:
        raise ValueError("未知演练")
    entries = []
    for item in record["stages"]:
        entries.append({"stage": item["name"], "at": item["started"], "status": item["status"]})
    return {"exercise": exercise, "entries": entries, "assets": [], "recovered": []}

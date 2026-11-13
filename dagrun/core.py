"""按给定顺序执行任务的最小实现。"""


def run(dag, workers=1):
    results = {}
    for name, task in dag.items():
        results[name] = task()
    return results

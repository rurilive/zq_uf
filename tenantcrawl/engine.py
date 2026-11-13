"""共享执行引擎：只按租户上下文执行任务，不持有任何全局状态。"""
from concurrent.futures import ThreadPoolExecutor


def execute(context, tasks):
    """用租户自己的 workers 并行执行任务，按提交顺序返回结果行。"""
    workers = context.config["workers"]
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_task, context, task) for task in tasks]
        for future in futures:
            rows.append(future.result())
    return rows


def _run_task(context, task):
    context.rate_limiter.acquire()
    return {
        "tenant": context.name,
        "task": task,
        "workers": context.config["workers"],
    }

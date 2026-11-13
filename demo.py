import time

from dagrun import Task, run


def print_records(title, result):
    print(f"\n== {title} ==")
    for name, record in result.records().items():
        line = f"{name:10} {record.status.value:8}"
        if record.started_at is not None:
            line += (
                f" start={record.started_at.strftime('%H:%M:%S.%f')[:-3]}"
                f" end={record.finished_at.strftime('%H:%M:%S.%f')[:-3]}"
                f" cost={record.duration:.3f}s"
            )
        if record.skip_reason:
            line += f" reason={record.skip_reason}"
        print(line)
    print("成功产物:", dict(result))


def sleep_task(name, value, duration=0.1):
    def task():
        time.sleep(duration)
        return value

    task.__name__ = name
    return task


def fail():
    raise RuntimeError("转换失败")


# 分叉再汇合：extract -> (transform_a, transform_b) -> load
DAG = {
    "extract": sleep_task("extract", {"rows": [1, 2, 3]}),
    "transform_a": Task(
        sleep_task("transform_a", "a-clean"), deps=["extract"]
    ),
    "transform_b": Task(
        sleep_task("transform_b", "b-clean"), deps=["extract"]
    ),
    "load": Task(
        sleep_task("load", "loaded"),
        deps=["transform_a", "transform_b"],
    ),
}

if __name__ == "__main__":
    ok = run(DAG, workers=2)
    print_records("分叉再汇合（workers=2）", ok)

    # 制造一次上游失败：下游 load 应被跳过，而非失败。
    flaky = dict(DAG)
    flaky["transform_b"] = Task(
        fail,
        deps=["extract"],
        timeout=10,
    )
    first = run(flaky, workers=2, max_timeout=30)
    print_records("上游失败，传导跳过", first)

    # 断点续跑：成功任务复用产物，只补跑失败和被跳过的任务。
    second = run(DAG, workers=2, resume=first)
    print_records("断点续跑", second)

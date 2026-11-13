# dagrun

纯 Python 标准库实现的批处理 DAG 依赖调度器。接口保持 `run(dag, workers)`，
支持按入度并行调度、失败传导跳过、断点续跑和可配置并发/超时。

## 快速开始

```python
from dagrun import Task, run

dag = {
    "extract": lambda: "raw",
    "transform_a": Task(lambda: "a", deps=["extract"], timeout=30),
    "transform_b": Task(lambda: "b", deps=["extract"]),
    "load": Task(lambda: "loaded", deps=["transform_a", "transform_b"]),
}

result = run(dag, workers=2)
print(dict(result))          # 任务名 -> 成功产物
print(result.records())      # 任务名 -> TaskRecord（完整状态）
```

无依赖任务可直接写可调用对象；有依赖时用 `Task(fn, deps=[...], timeout=...)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 状态机

每个任务有 5 个状态，状态只在主线程迁移：

```text
pending ──> running ──> success
   │          │
   │          └────────> failed（任务抛异常或超时）
   └──────────────────> skipped（上游 failed/skipped 的传导结果）
```

- `pending`：尚未就绪或等待调度；
- `running`：已进入线程池执行；
- `success`：正常返回，产物保存在记录中；
- `failed`：任务自身抛异常或超过超时上限，异常保存在 `record.error`；
- `skipped`：任意上游失败后，全部下游任务标记跳过，`record.skip_reason`
  记录触发跳过的上游任务和原始错误；跳过的任务不进入线程池执行。

## 调度与并发

- 按入度调度：入度为 0 的任务进入就绪队列，任务成功后释放其下游入度；
- 无关任务并行：线程池大小由 `workers` 指定（正整数），同时在跑的任务数
  不超过该值；`workers=1` 时按字典顺序串行执行；
- 超时上限：任务可声明 `timeout`（秒），实际超时取
  `min(任务声明值, max_timeout)`；未声明时使用 `max_timeout`，默认
  `MAX_TASK_TIMEOUT = 300` 秒。超时按 `failed` 处理并向下游传导跳过。
  Python 无法强杀线程，超时任务的迟到结果会被丢弃。
- 依赖不存在会抛 `KeyError`，DAG 中存在环会抛 `ValueError`。

## 重跑口径（断点续跑）

```python
first = run(dag, workers=2)
second = run(dag, workers=2, resume=first)
```

- 上一次 `success` 的任务：本次不执行，直接复用原记录和产物；
- 上一次 `failed` 或 `skipped` 的任务：本次重新参与调度（修复后即可跑通）；
- 续跑仍满足依赖关系：已成功的上游视为已完成，先释放下游入度；
- `resume` 接受 `RunResult`，也接受 `{任务名: TaskRecord}` 形式的记录字典。

## 运行记录

`run` 返回 `RunResult`：

- 字典视图（兼容旧接口）：仅包含成功任务，值为任务产物；
- `records()`：全部任务的 `TaskRecord`；
- `record(name)` / `status(name)`：查询单个任务；
- `TaskRecord` 字段：`status`、`result`、`error`、`started_at`、
  `finished_at`、`skip_reason`、`timeout`，时间为 UTC，另有
  `duration`（秒）辅助属性。

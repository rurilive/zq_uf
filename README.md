# jobdep

数据任务的 DAG 依赖与失败传播模块，纯 Python 标准库，测试线性执行。

## 解决的问题

- 上游失败后下游不再照跑：下游标记 `skipped` 并记录跳过原因，不产出空表。
- 失败有跳过标记，重跑只覆盖 `failed` / `skipped` 节点，`done` 节点复用。
- 依赖不再只存在于静态配置：运行中可校验后热更新依赖边。

## 接口

```python
from jobdep import run, rerun

dag = {"steps": [
    {"name": "extract", "fn": fn_extract},
    {"name": "transform", "fn": fn_transform, "deps": ["extract"]},
    {"name": "load", "fn": fn_load, "deps": ["transform"]},
]}
state = run(dag)                 # 按入度拓扑执行；失败时异常带 exc.state
state = rerun(exc.state)         # 也可传状态文件路径 rerun(state.state_path)
```

节点函数签名为 `fn(state)`，可在函数内调用
`state.update_deps(add=[("a", "b")], remove=[("c", "d")])` 热更新依赖
（元组表示有向边 `a -> b`）；更新先校验（未知节点、成环、落点已冻结
均抛 `DagValidationError`，更新不生效），当前节点函数返回后生效。

## 节点状态与跳过原因

| 状态 | 含义 |
| --- | --- |
| `pending` | 未调度 |
| `running` | 正在占用执行位 |
| `done` | 成功，重跑时复用、不重复执行 |
| `failed` | 函数抛异常，重跑集合成员 |
| `skipped` | 因上游失败或运行中止跳过，重跑集合成员，**不占用执行位** |

跳过原因通过 `state.reason(name)` 读取，值为 `[原因码, 触发节点]`：

- `["upstream_failed", <失败节点名>]`：失败节点的下游（含传递下游）被跳过，
  触发节点始终追溯到最初失败的节点。
- `["run_aborted", null]`：失败即停（或热更新校验失败中止）时，其余尚未
  执行的未决节点统一按此原因跳过；已经 `done` 的节点状态保留不变。

任一节点失败后，异常在传播与落盘完成后原样抛给调用方（异常带 `state`
属性），兼容“失败即停、下游不执行”的旧语义。

## 落盘与重跑口径

- 每次 `run` 在 `state_dir`（默认 `./.jobdep/<run_id>/`）下原子写
  `state.json`，含全部节点状态、跳过原因和依赖边；每次状态迁移都落盘。
- `rerun` **只从落盘 JSON 读取 `failed` / `skipped` 集合**，内存中被改过
  的状态不影响重跑范围；`done` 节点只读复用、不执行。
- 重跑结果合并后回写同一个 `state.json`，`reruns` 计数加一。
- 节点函数不可 JSON 化：跨进程按路径重跑时，需在同进程内先 `run` 过该
  DAG（模块内注册表提供函数），或直接传入 `run` 返回的 `RunState`。

## 演示与测试

```bash
python3 demo.py                      # 上游失败 -> 跳过 -> 修复后局部重跑
python3 -m unittest discover -s tests
```

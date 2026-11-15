# crawlflow

采集流程编排模块，纯 Python 标准库。阶段声明依赖（`depends_on`）、所需上下文字段
（`requires`）和产物（`produces`），引擎按入度拓扑执行；阶段处理函数用
`register_handler(name, func)` 注册，新增阶段不改框架代码。

## 接口

- `run(flow, context, token_ttl=3600)`：`flow` 为 dict、JSON 配置路径或
  `load_flow(path)` 的返回值；`flow` 为空时走默认 fetch→parse→store 线性流。
  返回 `stages`（各阶段状态/耗时）、`rerun`、`stats`（耗时与重跑次数）、
  `context`、`token`、`run_id`。
- `resume(token)`：凭令牌恢复，只重跑失败/被跳过的阶段及其下游；未知或过期
  令牌返回 `resumed: False`。
- `list_artifacts(run_id)` / `get_artifact(run_id, stage, name)`：查询落盘产物。

## 重跑口径

阶段失败时该阶段标记 `failed`、下游标记 `skipped`，独立分支照常执行；
`resume` 仅重跑这些阶段，重跑前只清理受影响阶段的产物目录，上游产物不动。
上下文显式传递且必须可 JSON 序列化，缺 `requires` 字段直接报 `FlowError`；
依赖成环、未知阶段名同样报错拒绝。

产物与令牌落盘在 `.crawlflow/`（`set_base_dir` 或 `CRAWLFLOW_HOME` 可改）。

```bash
python3 demo.py   # 分叉流程：fetch → parse_html/parse_meta → merge → store，含失败恢复
python3 -m unittest discover -s tests
```

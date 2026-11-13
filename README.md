# dagcrawl

采集任务的编排层，纯 Python 标准库。流程定义是可 JSON 序列化的任务图（DAG），
步骤声明依赖和产物，按入度调度执行；失败只重跑该步骤和下游。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 步骤定义

```python
flow = {
    "name": "site-a",
    "params": {"limit": 3, "min_len": 6},   # 默认参数
    "sites": {"site_b": {"limit": 2, "min_len": 7}},  # 按站点覆盖
    "site": "site_b",                        # 选用哪个站点（可选）
    "steps": [
        {"name": "list", "fn": "fetch_list", "provides": ["items"]},
        {"name": "detail", "fn": "fetch_detail", "needs": ["list"]},
        {"name": "clean", "fn": "clean", "needs": ["detail"]},
        {"name": "enrich", "fn": "enrich", "needs": ["detail"]},
        {"name": "store", "fn": "store", "needs": ["clean", "enrich"]},
    ],
}
ctx = dagcrawl.run(flow, {})
```

每个步骤是一个 dict，只允许以下键（出现未知键立即报 FlowError）：

- `name`（必填）：步骤名，全图唯一。
- `fn`（必填）：可调用对象 `fn(context) -> context`，或 `dagcrawl.register()`
  注册过的名字字符串。用字符串引用时整个 flow 可 `json.dumps` 序列化。
- `needs`：依赖的步骤名列表，默认 `[]`（无依赖，最先调度）。
- `provides`：声明产物（写入 context 的键）。步骤执行后校验，
  缺产物立即报 FlowError，不静默跳过。

旧格式（`steps` 直接是可调用对象列表）仍然支持，按声明顺序串成链执行。

## 调度与上下文

- 按入度做拓扑调度（Kahn），同层按声明顺序，执行顺序确定。
- 上下文通过 `context` dict 显式传递，不用全局变量；步骤返回新 dict 时
  会合并回调用方传入的 dict，调用方持有的 context 始终累积最新产物。
- 参数优先级：`flow["params"]` < `flow["sites"][site]` 覆盖 <
  调用方 `context["params"]`，合并后放在 `context["params"]` 供步骤读取。
- `dagcrawl.for_site(flow, "site_b")` 返回选用站点覆盖的流程副本，原流程不变。

## 失败与重跑口径

- 步骤抛错时原异常照常抛出（不包装、不吞），`flow["state"]` 记录各步骤
  状态：`done` / `failed`，未执行的步骤无记录。
- 用同一个 flow 和 context 再次调用 `run`：`done` 的步骤跳过，
  只重跑失败步骤及其下游（下游此前未执行，自然会被跑到）。
- `flow["state"]` 和 context 都是纯 JSON，`json.dumps` 落盘后，
  重启进程 `json.loads` 恢复即可从断点继续（步骤需用注册名字符串引用）。
- `dagcrawl.reset_state(flow)` 清空状态，强制全量重跑。

## 参数与阈值（demo）

| 参数 | 默认 | site_b 覆盖 | 含义 |
| --- | --- | --- | --- |
| `limit` | 3 | 2 | 抓取的列表条数 |
| `min_len` | 6 | 7 | 清洗阈值：保留长度 >= min_len 的条目 |

demo 跑 `list -> detail -> {clean, enrich} -> store` 分叉流程（site_b），
输出一行确定的 JSON，与 `tests/test_dag.py` 的断言逐字节一致：

```json
{"params": {"limit": 2, "min_len": 7}, "state": {"clean": "done", "detail": "done", "enrich": "done", "list": "done", "store": "done"}, "stored": ["ITEM-0", "ITEM-1"]}
```

## 错误口径

所有定义/编排错误抛 `dagcrawl.FlowError` 并指明原因：依赖成环、
依赖未知步骤、函数名未注册、步骤名重复、未知键、未知站点、缺声明产物；
步骤返回 `None` 抛 `RuntimeError`。异常路径不静默跳过。


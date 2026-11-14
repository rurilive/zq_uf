# jsrender

采集页面的渲染等待模块，纯 Python 标准库。按组合条件轮询等待，命中立即返回，
超时返回最后状态与快照差异，替代固定睡眠。

## 接口

```python
wait(page, condition, timeout=2.0, interval=0.1)
```

`page` 与 `condition` 是位置参数，旧代码 `wait(page, lambda p: ...)` 无需改动即可运行。
`timeout`、`interval` 为可选预算参数；另有关键字参数 `clock`、`sleep` 仅供测试注入。

## 四类条件

条件用字典描述，也可用 `jsrender` 提供的助手构造：

| 类型 | 字典写法 | 助手 | 命中判定（调用 page 协议方法） |
| --- | --- | --- | --- |
| 选择器 | `{"type": "selector", "target": ".card"}` | `selector(".card")` | `page.exists(target)` 为真 |
| 文本 | `{"type": "text", "target": "完成"}` | `text("完成")` | `page.text_contains(target)` 为真 |
| 网络空闲 | `{"type": "network_idle"}`（target 默认 true） | `network_idle()` | `page.network_idle() == target` |
| 脚本完成 | `{"type": "script", "target": "window.__done"}` | `script(...)` | `page.eval_script(target)` 为真 |

- 叶子可加 `"invert": true` 反转判定（如等待弹窗消失：selector 不出现即命中）。
- `network_idle(False)` 表示要求页面保持忙碌。
- 旧版可调用条件（callable）仍兼容，按 `condition(page)` 为真判定，回显时类型为 `callable`。

## 与或组合

```python
from jsrender import all_of, any_of, network_idle, selector, text, wait

# 与：选择器和文本都出现才算渲染完成
wait(page, all_of(selector(".card"), text("加载完成")))

# 或：任一就绪立即返回
wait(page, any_of(selector(".card"), text("加载完成")))

# 任意嵌套：（选择器 且 文本）或 网络空闲
wait(page, any_of(all_of(selector(".card"), text("加载完成")), network_idle()))
```

- `all`（与）要求组内全部叶子命中；`any`（或）要求组内至少一个叶子命中。
- 嵌套上限 32 层；同一层不能同时写 `all` 和 `any`。
- 叶子编号按深度优先的声明顺序（DFS），从 0 开始，体现在 id 后缀（如 `selector:.card#0`）。

## page 协议

page 是鸭子类型，按需实现以下方法；不实现的方法在对应条件被求值时抛 `TypeError`：

- `exists(css) -> bool`
- `text_contains(substr) -> bool`
- `network_idle() -> bool`
- `eval_script(code) -> bool`
- `snapshot() -> dict`（首末快照与差异的数据源）

字典 page 走兜底：`selectors`/`text`/`network_idle`/`scripts` 四个键模拟上述方法，
整体字典本身作为快照，便于轻量场景与单测使用。

## 预算与重试口径

- `timeout`：总预算秒数，默认 2.0，必须为正数。
- `interval`：基础重试间隔秒数，默认 0.1，必须为正数且不大于 `timeout`。
- 第 1 轮在 t=0 立即探测，之后每轮睡眠 `min(interval, 剩余预算)`：
  间隔随剩余预算收缩，最后一段不会睡过预算；预算耗尽前保证再做一次边界探测后才退出。
- 结果中 `waited` 回显总预算（保持与旧版字段兼容），`elapsed` 是实际耗时；
  `delays` 是每轮真实睡眠时长，可直接核对预算调整是否生效。

## 返回结果

命中（`ok=true`）：

```json
{
  "ok": true, "waited": 1.0, "elapsed": 0.15, "polls": 4,
  "delays": [0.05, 0.05, 0.05],
  "condition": {"any": [{"type": "selector", "target": ".card"}]},
  "hit": ["selector:.card#0"],
  "last_snapshot": {"selectors": {".card": true}}
}
```

超时（`ok=false`）额外给出排查现场：

- `last_state`：每个叶子在最后一轮的 id、类型与 matched 状态；
- `first_snapshot` / `snapshot`：首轮与末轮的 page 快照；
- `diff`：首末快照差异（见下）；
- `hit`：超时时仍为部分命中的叶子 id 列表（无任何命中则不输出该字段）。

## 命中顺序

- 每轮对全部叶子做全量探测，不做短路跳过，保证超时现场覆盖每个叶子。
- 叶子 id 后缀是声明顺序的 DFS 编号。
- 命中返回时，`hit` 先按**首次命中耗时**升序，耗时相同再按 **DFS 编号**升序；
  即"先就绪的排前面，同一轮就绪的按声明顺序排"。
- `any` 组合因此表现为"谁先就绪谁命中"；`all` 组合在全部就绪的那一轮一起命中，按声明顺序排列。

## 快照差异（diff）

快照先归一化（dict 按键名排序、序列类排序后比较），再按顶层键分三类输出，
未变化的键不出现在差异里；首末完全相同则 `diff` 为 `{}`：

- `added`：末轮新增的键；
- `changed`：值发生变化的键，形如 `{"before": ..., "after": ...}`；
- `removed`：末轮消失的键。

## 参数校验

等待开始前一次性校验，非法参数直接抛 `ValueError`，不消耗预算：
condition 类型未知、叶子缺 target 或类型错误、`invert` 非布尔、空组合、
同层 `all`/`any` 并存、组合值不是非空列表、未知字段、嵌套超深、
`timeout`/`interval` 非正数或 `interval > timeout`。
page 不支持某条件所需协议方法时，在求值时抛 `TypeError`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

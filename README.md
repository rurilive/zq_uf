# wafregress

WAF 规则回归工具：规则改动后用固定用例集自动回归，**拦截侧和放行侧一起看**，
变更影响执行前可见，结果按规则和用例留存、可回溯、可对比。

## 解决的问题

- 改一条规则要人工逐条发请求，回归几百条用例；
- 拦截率上去的同时正常请求被误拦，旧版只统计攻击侧漏拦，业务用例被拦不标记；
- 回归结果不留存，下次改动又从头来，无法回答「这次改动误伤了哪些正常业务」。

## 四种判定结果（两侧覆盖）

| 结果 | 含义 | 用例期望 | 实际 |
| --- | --- | --- | --- |
| `blocked` | 正确拦截 | block（攻击用例） | 被拦 |
| `missed` | 漏拦（保留触发请求，可直接复现） | block | 放行 |
| `false_positive` | 误拦（按业务线/场景分组，附原始请求） | pass（业务用例） | 被拦 |
| `passed` | 正常放行 | pass | 放行 |

## 快速开始

```python
from wafregress import regress, report, impact, compare, reset

reset()
rules_v1 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<script>"},
]
regress(rules_v1)                       # 改动前全量基线

rules_v2 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<"},                                   # 规则放宽
    {"id": "login_sqli", "pattern": "'--", "scope": "login"},        # 新增，限 login 业务线
]

impact(rules_v2)                        # 执行前看变更影响范围（受影响用例 + 业务线分布）
regress(rules_v2, only_affected=True)   # 只跑受影响用例
report()                                # 误拦分组、漏拦请求、按规则统计
compare()                               # 改动前后两轮对比
```

完整可运行示例：`python3 demo.py`（含文件留存演示）。

## 入口说明

### `regress(rules, only_affected=False, biz=None)`

跑一轮回归并留存，返回汇总计数与变更影响：

```python
{
  "run_id": 2, "rules": 3, "cases": 4,
  "blocked": 2, "false_positive": 2, "missed": 0, "passed": 1,
  "impact": {"changed_rules": {...}, "affected_cases": [...], "by_biz": {...}},
}
```

- `only_affected=True`：相对上一轮规则，只跑受新增/删除/修改规则影响的用例；
- `biz="pay"`：只跑指定业务线的用例；两者可组合。
- 规则可选 `scope` 字段限定业务线；不带 `scope` 的为全局规则。

### `report(run_id=None)`

查看某轮完整结果（默认最近一轮）：

- `blocked`：命中拦截明细（用例 + 命中的规则）；
- `false_positives.by_scenario`：误拦按 **业务线 → 业务场景** 分组，
  每条带 `request` 和 `blocking_rule`，可直接拿来复现；
- `missed`：漏拦用例保留触发请求与期望规则；
- `by_rule`：误拦/漏拦/正确拦截**按规则统计**；
- `by_biz`：按业务线的两侧统计。

### `impact(rules, biz=None)`

执行前对比上一轮规则快照，返回新增/删除/修改的规则、受影响用例 id 列表、
按规则的影响明细和按业务线的计数。没有历史基线时抛 `NoBaselineRun`。

### `compare(base_run_id=None, target_run_id=None)`

对比两轮（默认最近两轮）：用例结果流转、`new_false_positives`、
`resolved_false_positives`、`new_missed`、`resolved_missed` 和汇总差值。

### 用例集与留存

- 用例按业务线分文件维护，目录下每个 `<biz>.json` 对应一条业务线，
  改动只影响对应业务线：

  ```python
  from wafregress import load_cases
  load_cases(["examples/cases"])          # 目录或单个 json 文件
  ```

- 默认内存留存；需要跨进程/跨次改动留存时切到文件存储（原子写）：

  ```python
  from wafregress import configure
  configure(store_path="./waf-runs")      # 历史 run 落盘，重启后可查可对比
  ```

- `reset()` 清空历史回归结果（保留已加载的用例集）；`list_runs()` 列历史轮次。

## 用例与规则字段

用例（`expect` 取 `block`/`pass`）：

```json
{
  "id": "p2", "biz": "pay", "scenario": "note",
  "request": "/pay?note=thanks for the <invoice>",
  "expect": "pass", "expect_rule": "xss", "description": "可选"
}
```

规则（当前为子串匹配，`scope` 可选）：

```json
{"id": "xss", "pattern": "<script>", "scope": "pay", "description": "可选"}
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 目录结构

```
wafregress/
  models.py    # 用例/规则模型、匹配与四种结果判定
  cases.py     # 按业务线加载用例集
  impact.py    # 规则 diff 与变更影响分析
  report.py    # 误拦分组、按规则统计、前后对比
  store.py     # 内存/文件留存
  engine.py    # 引擎与 regress/report/compare/impact 模块级入口
examples/cases/  # 按业务线维护的示例用例
tests/           # 单元测试
```

# jsonshape

采集响应的结构巡检模块，纯 Python 标准库。按**站点**各自维护最近窗口的
结构基线，发现字段增删、类型漂移和缺失率突升时分别告警，冷启动只学习不
告警，连续多批异常才升级。

## 结构指纹

每条响应经 `fingerprint(payload)` 递归展开为「路径 -> 结构」指纹：

- 路径：`$` 为根，对象用 `.字段`（如 `$.seller.name`），数组元素用 `[下标]`
  （如 `$.tags[0]`）。
- 类型：`bool / int / float / str / list / dict / null`，一个路径在窗口内
  出现过的全部类型都会记入基线。
- 数组长度分布：记录每次出现时的长度，基线汇总为 `histogram / min / max /
  avg`，可发现列表规模漂移。

## 基线与阈值

每个站点独立保存状态，阈值集中在 `jsonshape/shape.py` 顶部：

| 常量 | 默认 | 含义 |
| --- | --- | --- |
| `MIN_SAMPLES` | 3 | 冷启动批数，不足时只学习不告警 |
| `WINDOW_SIZE` | 20 | 最近窗口大小，基线与缺失率只看该窗口 |
| `RELIABLE_PRESENCE` | 0.8 | 出现率 ≥ 该值才算稳定字段，缺失按字段消失处理 |
| `MISSING_RATE_DELTA` | 0.3 | 可选字段近期缺失率比基线高出该比例即告警 |
| `ESCALATE_BATCHES` | 3 | 连续异常批数达到后由 warning 升级为 critical |

冷启动结束时冻结一份参考快照（出现率/缺失率基准），之后基线只吸收正常
批次，异常批次不污染基线；恢复正常后连续异常计数清零，再次异常重新从
warning 计起。

## 告警类型

- `field_added`：出现基线中没有的路径，`baseline=None`。
- `field_removed`：稳定字段（出现率 ≥ `RELIABLE_PRESENCE`）在当前批次缺失。
- `type_changed`：当前类型集合出现基线之外的类型（如 `int` → `str`）。
- `missing_rate_spike`：可选字段窗口缺失率较基线突升 ≥ `MISSING_RATE_DELTA`。

每条告警都带 `site / kind / path / baseline / current / sample_page /
severity / consecutive`，`sample_page` 为该站点内的批次序号，`current` 内含
截断后的样本值（缺失时为 `<missing>`）。

## 用法

```python
from jsonshape import observe, report, reset

reset()
observe("shop", {"id": 1, "price": 2})          # 冷启动只学习
result = observe("shop", {"id": 1, "price": "2"})  # 基线建立后告警
report("shop")                                   # 只看某站点；report() 看全部
```

接口保持 `observe(site, payload)` 与 `report(site)`，另导出 `fingerprint`
便于查看单条响应的指纹。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

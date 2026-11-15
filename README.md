# proxysettle

采集流量出口计费模块（纯 Python 标准库）。按采集结果计费，失败/超时/出口切换退费，按天落盘并与入库行数对账。

## 计费与退费口径

| 采集结果 | 判定参数 | 口径 |
| --- | --- | --- |
| 抓取成功且页面入库 | `success=True, stored=True`（默认） | 全额计费，不退费 |
| 抓取成功但未入库 | `stored=False` | 全额退费 |
| 抓取失败 | `success=False` | 全额退费 |
| 抓取超时 | `timeout=True` | 全额退费 |
| 出口切换 | `switch_proxy(...)` | 旧出口在该批次内**未结算**的计费全额退费，切换记录关联原出口、新出口与批次 |

退费比例集中在 `proxysettle.REFUND_RULES`，当前失败/超时/未入库/切换均为全额退费（`1.0`），可按站点规则调整。金额按两位小数处理。

## 接口

- `charge(proxy, amount, *, success=True, stored=True, timeout=False, batch="default")`：记一笔采集调用并按结果计费/退费，返回余额、退费与口径。旧式 `charge(proxy, amount)` 仍表示成功入库。
- `settle(proxy, date=None, stored_rows=None)`：按天结算，返回计费 `charges`、退费 `refunds`、余额 `balance`、计费条数与入库行数差异 `diff = 计费条数 - 入库行数`，结果落盘到 `<data_dir>/settlements/YYYY-MM-DD.json`。
- `switch_proxy(old, new, batch)`：切换出口，退旧出口批次费用并留关联记录。
- `mark_stored(proxy, rows, date=None)`：登记入库行数供对账；`init(data_dir)` 启用落盘，重启后账本与日结算单自动恢复。

```bash
python3 -I demo.py
python3 -I -m unittest discover -s tests
```

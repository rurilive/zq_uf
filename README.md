# fieldpatch

采集结果按**字段**修补，缺一个字段不再丢掉整行。

## 策略

| 情形 | 处理 | 来源标记 |
| --- | --- | --- |
| 必填字段缺失 / 类型非法 | 隔离该记录（其余可空字段仍补齐） | `none` |
| 可空字段缺失 | 按类型补默认值（数字 `0`、布尔 `False`、字符串 `""`，绝不补空串给数字） | `default` |
| 枚举越界 | 降级到 `degrade` 值并记录原值 | `degrade` |
| 类型可收敛（如 `"12"`→`12`） | 收敛并保留原值 | `original` |
| 字段齐全 | 原样透传 | `original` |

每条逐字段留痕都带 `batch_id / day / site / source / reason / original / value`。

## 能力

- `run_batch(..., dry_run=True)`：先在单站点试运行，不落库，确认补齐结果能被下游读取
- `report(day, site)`：按天 + 站点汇总修补、隔离、来源分布与缺失比例（区分必填缺失）
- 缺失比例超 `missing_threshold` 自动告警
- `rollback(batch_id)` / `replay(batch_id)`：按批次回滚与回放，统计随活跃批次动态变化
- `export_patches(day, site, batch_id, fmt="csv")`：值班同学按站点/天导出留痕

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 模块

- `fieldpatch/schema.py`：schema 归一化与单字段校验（纯函数）
- `fieldpatch/patch.py`：行级修补（无状态）
- `fieldpatch/store.py`：批次编排、留痕、统计、告警、回滚/回放、导出

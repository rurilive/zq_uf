# schemamigrate

表结构双向迁移，纯 Python 标准库。每步迁移声明 forward/backward 变换，
升级按序执行，回退逆序执行；失败停在该步并保留现场。

## 版本链

- v1：`name`, `contact`
- v2：+`email`（与 `contact` 双写并存，读时互为兜底）
- v3：+`first_name`/`last_name`（与 `name` 双写并存）

## 校验口径

- 校验和：对全部行做 JSON 规范化序列化（`sort_keys`）后的 SHA-256。
- 每步前后记录行数与校验和；行数变化必须等于该步声明的
  `expect_rows_delta`，否则该步失败。
- 失败时停在该步、不自动回滚，结果中的 `rollback_point` 指向最近完好版本。
- 每次迁移记录版本、操作人（`operator`）与耗时（`duration`），
  见 `migration_log()`。

## 用法

- `upgrade(version)` / `downgrade(version)`：迁移到指定版本，返回每步结果、
  校验和与回退点；`downgrade` 只支持回退到更低版本。
- `insert(row)`：字段并存期对新旧字段双写；`read_field(row, field)`：读时兼容。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

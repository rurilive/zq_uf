# dqcontract

现状：契约写死只查首个必填字段，失败只记日志。

问题：阈值改要发版，坏数据照常入库，契约与结构不同步。

目标：可配置非空、范围、枚举和唯一性，按级别隔离或告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：

- `register_contract(contract, schema, approved_by)`：变更走审批，规则版本随表结构版本登记；规则引用已删字段直接拒绝。
- `check(batch, contract, schema)`：按表字段声明 not_null / range / enum / unique；`severity: block` 隔离、`warn` 告警；返回通过、隔离、告警、规则版本和结构漂移提示。
- `enforce(result)`：有隔离即阻断入库，告警只记录。
- 规则加 `mode: shadow` 影子运行，返回隔离比例，确认后再改 `active`。
- `recheck(rows, contract)` 复检修复后的隔离数据；`failure_samples(batch_id)` 按批次回看失败样本。

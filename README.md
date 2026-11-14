# fieldpolicy

四层可继承的字段脱敏策略，纯 Python 标准库。

## 继承口径

- 四层：global → domain → table → field，生效值为各层取或（OR）。
- 下层只能收紧不能放宽：上层已脱敏时下层设 False 抛 ValueError。
- 域按表名点号前缀推断，或用 set_table_domain 显式指定。
- resolve(table, field) 返回 masked、source_layer、exemption。

## 豁免口径

- grant(table, field, until, approver=...) 登记豁免，until 为 epoch 秒。
- 到期自动失效，失效时写审计，audit_log() 可查。
- 生效豁免使 masked 为 False，过期后恢复脱敏。
- write_out(table, record) 是写出前统一脱敏的唯一出口，不可绕过。

## 运行

    python3 demo.py
    python3 -m unittest discover -s tests

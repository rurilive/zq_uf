# maskctl

采集数据落库前的字段脱敏模块，纯 Python 标准库。规则脱敏 + 受控还原，
接口为 `mask(row)` 和 `reveal(row, token)`。

## 脱敏规则

- `RULES[table][field] = {"prefix": n, "suffix": m}`：保留前 n 位、后 m 位，中间以 `*` 替换。
- 规则解析顺序：表内规则 → `default` 表规则 → 默认策略（`DEFAULT_POLICY`，保留首 1 位）。
- 未配置字段按默认策略处理，新增采集字段自动继承，不会漏脱敏。
- 值太短（长度 ≤ prefix + suffix）或非字符串时原样跳过，并记 `skipped` 审计。
- 密钥版本化：`KEYS` 存各版本密钥，令牌前缀即签发版本；`rotate_key` 轮换后旧令牌仍可还原。

## 权限口径

- 角色：`writer`（仅脱敏）、`integrator`（脱敏+还原）、`admin`（另可审批）。
- `mask()` 返回 `{row, masked, token, audit}`：脱敏行、脱敏字段、还原令牌、本次审计。
- `reveal()` 需令牌有效且签发角色具备 `reveal` 权限；失败即告警（`ALERTS`）并记录请求人。
- 规则变更（`set_rule`）与密钥轮换（`rotate_key`）需 `APPROVERS` 中的审批人，否则抛 `PermissionError`。
- 全部操作写 `AUDIT_LOG`；`export_inventory()` 按表导出脱敏清单（可写 JSON 文件）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

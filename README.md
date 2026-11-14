# tabledesens

数仓字段脱敏与受控还原模块，纯 Python 标准库实现。按血缘下发脱敏策略，
敏感字段不再原样落盘，联调通过「令牌 + 权限 + 审计」受控拿回原值。

## 策略与严格度

| 策略 | 严格度 | 效果 | 可逆 |
| --- | --- | --- | --- |
| `NONE` | 0 | 明文不处理 | — |
| `MASK` | 1 | 掩码（如 `138******00`），原值加密存入令牌库 | 凭令牌还原 |
| `HASH` | 2 | HMAC-SHA256 假名（`h_` 前缀），同值同假名可关联 | 不可逆 |
| `REDACT` | 3 | 抹成 `None`，原值不落任何存储 | 不可逆 |

## 血缘规则口径

- 血缘是有向无环图，边 `parent -> child` 表示字段流向；`Lineage.add_rules`
  在节点上配置 `{字段: 策略}`，也可用 `default=` 配置节点级默认。
- 规则沿血缘向下继承，多父节点汇入时取**最严格**策略。
- 下游只能收紧（严格度只能持平或升高）；放松约束在配置阶段直接抛
  `PolicyLooseningError`，含试图改回 `NONE`。
- 未配置字段不会漏：引擎按 `default_strategy` 兜底处理，并在
  `MaskResult.missed` 中逐字段上报，治理侧据此补规则。默认策略为 `MASK`。
- 血缘成环抛 `PolicyError`。

## 权限口径

- `mask` 是安全侧操作，不设权限，输出只有脱敏行、令牌和审计。
- `reveal` 必须同时满足：① 令牌签名合法、归属当前节点且未吊销；
  ② 操作人持有该节点的还原授权（`RevealPermission.grant`，`"*"` 为
  全节点通配）；③ 令牌所用密钥版本仍在密钥环中。匿名调用一律拒绝。
- 令牌可由 `engine.revoke_token` 吊销，**吊销立即生效且幂等**；权限本身
  也可单独撤销（`permission.revoke`），两者相互独立。
- 密钥版本化（`Keyring.rotate`）：新令牌用当前版本，旧版本保留以解密
  历史令牌；轮换后旧令牌仍可还原，删除旧版本则旧令牌失效。
- 所有 `mask`、成功/失败的 `reveal`、`revoke` 都写审计。`AuditLog`
  传目录后按 `<UTC日期>/<操作人>/` 归档 JSON 文件，即按操作人和时间
  两个维度可检索；不传目录只留内存。

## 接口

模块级 `mask(row)` / `reveal(row, token)` 签名保持不变；未注入引擎时
维持历史直通行为（既有调用方不受影响），新代码显式构造引擎：

```python
from tabledesens import Lineage, DesensEngine, MASK, HASH, REDACT

lineage = Lineage(edges=[("ods.user", "dwd.user")],
                  rules={"ods.user": {"phone": MASK, "id_card": HASH}})
lineage.add_rules("dwd.user", {"id_card": REDACT})  # 下游收紧

engine = DesensEngine(lineage, node="dwd.user", default_strategy=MASK)
result = engine.mask(row, operator="etl")
result.row       # 脱敏行，可落盘
result.tokens    # {字段: 令牌}，单独通道保管
result.audit     # 审计事件
result.missed    # 走默认策略兜底的遗漏字段

engine.permission.grant("analyst", "dwd.user")
restored = engine.reveal(result.row, result.tokens, operator="analyst")
restored.row     # 还原后的行
```

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

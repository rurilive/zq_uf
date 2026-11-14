"""联调演示：血缘下发脱敏策略 -> 落盘脱敏表 -> 受控还原 -> 吊销失效。

场景：
- ods.user 配置 phone= MASK、id_card=HASH，规则沿血缘流到 dwd.user；
- dwd 把 id_card 收紧到 REDACT（下游只能收紧）；
- ETL 用引擎脱敏后只把脱敏行落盘，令牌单独保管；
- 联调同学 analyst 持令牌 + 节点权限申请还原，成功；
- 无权限的 guest 被拒；安全运营吊销令牌后，analyst 再次还原立即失败；
- 全过程审计按操作人/时间归档到临时目录。
"""

import tempfile

from tabledesens import (AuditLog, DesensEngine, Lineage,
                         MASK, HASH, REDACT, PermissionDeniedError,
                         TokenRevokedError)

lineage = Lineage(
    edges=[("ods.user", "dwd.user")],
    rules={"ods.user": {"phone": MASK, "id_card": HASH}},
)
# 下游收紧：HASH -> REDACT，允许；尝试放松会抛 PolicyLooseningError
lineage.add_rules("dwd.user", {"id_card": REDACT})

with tempfile.TemporaryDirectory() as audit_dir:
    engine = DesensEngine(
        lineage, node="dwd.user", default_strategy=MASK,
        audit=AuditLog(audit_dir))

    raw = {"phone": "13800000000", "id_card": "310101199001011234",
           "nickname": "alice"}  # nickname 未配置 -> 默认策略兜底并上报

    # 1) ETL 脱敏：落盘的只有脱敏行；令牌走单独通道
    result = engine.mask(raw, operator="etl-bot")
    print("脱敏落盘行:", result.row)
    print("遗漏字段(走默认策略):", result.missed)
    print("令牌单独保管:", {k: v[:24] + "..." for k, v in result.tokens.items()})

    # 2) 联调还原：analyst 被授权该节点，凭令牌拿回原值
    engine.permission.grant("analyst", "dwd.user")
    restored = engine.reveal(result.row, result.tokens, operator="analyst")
    print("analyst 还原:", restored.row)

    # 3) guest 没有权限，直接拒绝且留审计
    try:
        engine.reveal(result.row, result.tokens, operator="guest")
    except PermissionDeniedError as exc:
        print("guest 被拒:", exc)

    # 4) 安全运营吊销令牌；吊销后 analyst 再还原立即失效
    engine.revoke_token(result.tokens["phone"], operator="secops")
    try:
        engine.reveal(result.row, result.tokens, operator="analyst")
    except TokenRevokedError as exc:
        print("吊销后还原失败:", exc)

    # 5) 审计归档：按 UTC 日期/操作人分目录
    print("审计事件数:", len(engine.audit.events))
    print("analyst 的审计:",
          [(e["action"], e.get("fields", e.get("reason")))
           for e in engine.audit.query(operator="analyst")])
    print("审计归档目录:", audit_dir)

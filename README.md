# maskctl

现状：敏感字段原样返回，脱敏和还原都没有实现。

问题：手机号地址明文落盘，联调拿不到原值，新增字段漏配。

目标：按规则脱敏并保留令牌，受控还原写审计，默认策略兜底。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from maskctl import MaskCtl, Rule, RuleRegistry

registry = RuleRegistry(approvers={"lead"}, oncall={"oncall"})
ctl = MaskCtl(registry=registry)
cid = registry.submit_change("dev", "users", "phone", rule=Rule(3, 4))
registry.approve(cid, "lead")                       # 规则变更走审批
r = ctl.mask({"phone": "13800000000"}, table="users")  # 138****0000
ctl.grant_reveal("dev", "users", granted_by="lead")    # 还原需授权
ctl.reveal(r["token"], requester="dev")                # 还原并写审计
```

未配规则的字段按默认策略全遮蔽，新增字段自动继承；处理清单 `ctl.manifest.export(表)`、
审批记录 `registry.export_approvals(viewer=值班)` 均可导出；规则变更后历史数据不重新处理。

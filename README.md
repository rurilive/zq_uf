# maskctl

现状：敏感字段原样返回，脱敏和还原都没有实现。

问题：手机号地址明文落盘，联调拿不到原值，新增字段漏配。

目标：按规则脱敏并保留令牌，受控还原写审计，默认策略兜底。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

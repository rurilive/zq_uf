# tabledesens

现状：脱敏和还原都原样返回。

问题：敏感字段明文，联调无法受控还原，新增字段漏脱。

目标：按血缘下发，密钥版本化，受控还原和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

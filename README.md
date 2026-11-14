# fieldpolicy

现状：全局一个脱敏开关，表和字段没有独立策略。

问题：要么全脱要么全不脱，表级覆盖绕过列级，豁免永久生效。

目标：四层继承只能收紧，豁免带期并审计，写出前统一执行。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

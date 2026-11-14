# auditchain

现状：审计事件按行追加，验证函数恒返回通过。

问题：日志可被静默篡改，轮转后链接不上，全量校验很慢。

目标：哈希链，跨文件验证，区间校验和断链定位。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

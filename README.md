# sessionvault

现状：会话明文写JSON，键固定，没有版本和校验。

问题：凭据可读，密钥轮换半途失败，文件损坏整个账号不可用。

目标：带版本密钥的加密落盘，轮换可回滚，损坏记录隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

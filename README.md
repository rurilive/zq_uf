# lakechecksum

现状：整文件算 SHA256，修复是空操作。

问题：大文件慢，坏块整文件重下，清单不同步。

目标：分块校验，坏块修复，清单同步和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

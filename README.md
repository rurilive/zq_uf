# compact

现状：把目录下所有文件按名字合并成一个。

问题：小文件仍很多，合并期间新写入会漏，失败留半个结果。

目标：按大小策略、参与清单、原子改名和校验回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

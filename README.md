# manifestcheck

现状：清单直接覆盖写，校验只看文件在不在。

问题：缺文件发现不了，大小和校验和不比对，写坏整批失败。

目标：清单记录大小和校验和，原子写，校验缺失、多余和损坏。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

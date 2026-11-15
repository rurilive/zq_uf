# attachcheck

现状：附件一次写完，不看断线和重名。

问题：半截文件留下，同名覆盖，损坏无法发现。

目标：断点续传，内容哈希分目录，校验后原子改名。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

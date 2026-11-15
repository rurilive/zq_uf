# partclone

现状：直接复制分区数据，不取快照，失败留下半份。

问题：克隆期间源分区还在写，目标不一致，行数对不上才发现。

目标：一致性快照后复制，失败清理并从断点继续，完成后校验。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

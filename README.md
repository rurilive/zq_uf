# snapver

现状：创建快照时整目录复制，回滚直接覆盖。

问题：复制到一半的文件也进快照，没有元数据，重复占用空间。

目标：一致视图、清单校验、内容去重和校验失败拒绝回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

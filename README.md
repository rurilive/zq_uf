# jobrecover

现状：检查点只存阶段名，恢复从起始阶段重来。

问题：重复抓已入库页面，检查点写坏无法判断，并发任务互相覆盖。

目标：检查点记录阶段、位点和校验和，损坏回退上一有效点，按任务隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

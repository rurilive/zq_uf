# taskresume

现状：检查点只放内存，恢复时从零重跑。

问题：检查点和入库不同步，恢复重复写，损坏回退很远。

目标：检查点记录入库位点和校验和，损坏回退到上一有效点，按任务隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

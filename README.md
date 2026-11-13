# slapredict

现状：所有任务共用一个历史平均值，预测区间宽度为零。

问题：数据量翻倍就延误，不同任务混算偏差大，没有区间不敢排期。

目标：按任务类型和数据量拟合，近期样本加权，剔除异常并可回测。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

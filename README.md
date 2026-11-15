# sitehealth

现状：只累计事件数，评分固定满分。

问题：慢站和限速站分很高，故障被历史稀释，没有分项。

目标：按成功率、延迟、限速和内容异常加权，快降慢升。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

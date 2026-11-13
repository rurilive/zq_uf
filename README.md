# formfill

现状：按顺序批量填报报销单，字段做日期和金额的简单转换。

问题：弹窗会卡住，重试开出重复单据，不同模板格式错位。

目标：并发且幂等的填报流程，带回执核对和失败清单。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

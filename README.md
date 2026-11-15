# tablesample

现状：按比例随机抽行，不固定种子。

问题：两次抽样结果对不上，长尾采丢，误差说不清。

目标：可复现分层采样，固定种子，输出均值、分位数和误差范围。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

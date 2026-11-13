# sampler

现状：按条数做无分层随机抽样，seed 和分层参数不生效，摘要只有均值。

问题：两次采样对不上，长尾被采丢，分位数偏差大，误差无法说明。

目标：可复现的分层采样，固定种子，输出均值、分位数和误差范围。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

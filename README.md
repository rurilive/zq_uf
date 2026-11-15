# pagecore

现状：去掉标签后整页返回，导航和推荐位都算正文。

问题：字数虚高，短正文被丢，规则写死改版就失效。

目标：按块打分抽正文，短正文保留并给置信度，规则按站点覆盖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

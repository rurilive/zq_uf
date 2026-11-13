# dupfinder

现状：正文完全相等才算重复。

问题：改标题就漏，长短文本相似度失真，百万级跑不完。

目标：分片指纹、阈值召回、复核和多级索引。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

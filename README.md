# urlcanon

现状：只做字符串相等和直接合并，没有规则和传递。

问题：端口、默认页、编码和追踪参数产生重复，等价关系不传递。

目标：规则优先级加并查集，代表稳定，索引可持久化。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

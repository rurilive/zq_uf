# linkgraph

现状：只记录一跳出链，环路分析永远返回空。

问题：跨页环看不出，参数变体被当新节点，大图递归爆栈。

目标：归一化建图，迭代找环和强连通分量，支持截断。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

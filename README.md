# pagesnap

现状：按页面 id 存文本，比较长度和内容。

问题：结构和样式丢失，重复内容占空间，看不出节点变化。

目标：去重存储、索引取回、结构比对和保留天数。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

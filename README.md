# changedetect

现状：整段文本比较，有差异就重新入库。

问题：广告和布局变化触发重复入库，节点移动算删除加新增，差异不分级。

目标：结构对齐，忽略噪声，字段级差异和入库分级。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

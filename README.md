# seedexpand

现状：种子页面的链接一次性全部入队，只做一跳。

问题：没有深度和每层上限，重复目标跨批不去重。

目标：按深度和配额扩散，跨批去重，记录来源。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

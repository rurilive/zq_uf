# contenthash

现状：整页原文取SHA256，任何噪声都会改变指纹。

问题：改一个广告位就判新内容，空白差异也重复，短正文误判多。

目标：剔除模板噪声，按块取稳定指纹，按重合率判近重复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

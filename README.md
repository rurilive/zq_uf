# listwatch

现状：按标题集合比对两次列表页，输出新增和删除。

问题：改标题就漏，顺序变化全判新增，删除发现不了。

目标：稳定键、内容指纹、跨页去重、真删除识别和增量告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

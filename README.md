# harvestcfg

现状：单个 JSON 文件直接加载，没有校验和继承。

问题：字段拼错运行时才报，共享规则靠复制，改限速要重启。

目标：多层合并、选择器校验、热更新和差异预览。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

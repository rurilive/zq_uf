# encodepage

现状：只按响应头的 charset 解码，没有声明就按 UTF8。

问题：头缺失就乱码，声明和实际不一致直接报错。

目标：多级回退、冲突判定、替换字符检查和置信度。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

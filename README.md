# csvexport

现状：用默认方言写 CSV，编码可传。

问题：中文乱码、长数字变科学计数、字段换行错位。

目标：BOM、方言配置、文本化编号、流式写入和分片续传。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

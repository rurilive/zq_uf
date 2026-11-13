# extractconf

现状：字段正则写在配置里，按顺序取第一个命中。

问题：站点改版要改配置，但没有回退、默认值和错误定位。

目标：选择器与正则混合、顺序回退、类型转换和结构校验。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

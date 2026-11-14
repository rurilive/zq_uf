# apipage

现状：按页码和 next_page 翻页，断点只记页码。

问题：游标过期整批失败，重复游标死循环，恢复重复抓。

目标：游标加断点，过期重取，重复停止和已抓跳过。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

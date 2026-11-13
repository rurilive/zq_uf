# pagedigest

现状：按固定页码翻商品分页接口，单页解析字段，遇到空页停止。

问题：接口改成游标后翻页重复，空页被当失败，进程中断只能从头再抓。

目标：可恢复的游标抓取，带限速、断点和去重。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

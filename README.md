# filerouter

现状：按后缀移动到对应目录。

问题：日志和压缩包混放，同名覆盖，失败留在原地。

目标：多条件规则、重名加序号、待处理目录和优先级。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

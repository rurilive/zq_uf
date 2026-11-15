# linkrewrite

现状：重写函数原样返回链接，规则表为空。

问题：分页和追踪参数混在一起，规则写死，重写无记录。

目标：按站点配置保留、删除和注入参数，冲突按优先级解决。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

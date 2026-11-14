# sitepolicy

现状：默认策略加站点覆盖，更新直接改全局字典。

问题：冲突看不出以谁为准，热更新读到半新半旧，没有审计。

目标：三层继承、字段级冲突记录、原子切换和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

优先级：默认 < 分组 < 站点（resolve(site, overrides) 的临时覆盖最高）。
站点经站点层 group 字段挂到分组；同名字段高层胜出，
冲突按字段记录在 resolve() 返回值的 conflicts（来源层、各自取值、胜出层）。

更新口径：update(patch, operator) 先深拷贝生成新快照并校验
（rate/timeout 为正数、retry 为非负整数），通过才单次赋值原子切换，
正在执行的 resolve 只会读到完整旧快照或完整新快照；
校验失败抛 ValueError，旧快照原样保留，即为回滚。
每次更新（含被拒绝的）都写审计：audit() 可见操作人、
逐字段前后值、版本号、状态和失败原因。

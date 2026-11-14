# depcycle

现状：只按配置读依赖，环检测直接返回空。

问题：写出环后任务互相等待，热更新绕过检查，只报错不给断边建议。

目标：入度遍历找环和强连通，增量检查，按权重给最少断边。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）

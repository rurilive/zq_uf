# harvestcfg

采集任务配置中心，纯 Python 标准库。

层级（优先级低→高）：default < site < task < tenant，逐层合并，headers/retry/selectors/patterns 按键深合并，其余整体覆盖；同层多来源同字段取值不同即报冲突（带字段和来源）。共享规则用 profiles + extends 继承，循环引用加载时报错。选择器、正则做语法校验，字段拼错给建议；敏感字段（api_key、proxy_auth）只存引用（如 ${env:NAME}），不落明文。

热更新：watch(dir) 监听目录下 default/site/task/tenant.json 和 profiles/，按内容指纹判断真实变化；先生成差异预览再生效，校验失败自动保持上一份可用配置；rollback() 回滚上一版本。resolve() 输出最终配置、逐项来源链（source_chain）和版本记录（watcher.versions）。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

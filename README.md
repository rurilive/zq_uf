# versionflow

可编排的数据集版本切换：一次 `switch(dataset, version)` 按顺序执行
`stop_writes → switch_dir → update_metadata → clear_cache` 四步，纯标准库实现。

## 切换步骤

1. `stop_writes`：暂停数据集写入；回退时恢复写入。
2. `switch_dir`：切换数据目录指针（数据集根目录下的 `CURRENT` 文件）；回退时指回上一版。
3. `update_metadata`：写 `metadata.json`（当前版本、上一版、操作人、原因、时间）；回退时还原旧内容。
4. `clear_cache`：清理非目标版本的下游缓存条目；回退时还原被清条目。

## 回退口径

任一步失败即中止：已完成步骤按逆序补偿，版本恢复到上一版，审计记录状态为
`rolled_back`，并抛出 `SwitchError`（携带失败步骤名和每步结果）。异常路径一律
显式报错，不静默跳过；补偿本身失败会追加进错误消息。

## 参数与边界

- `switch(dataset, version, operator="system", reason=None, pipeline=None)`：
  返回 `from/to/step_results/final_version/rolled_back`；`steps` 为兼容字段，固定 `["pointer"]`。
- `register_dataset(name, versions, current=None)`：注册后版本集合固定，
  切到未注册版本抛 `ValueError`；未注册数据集走 `switch` 自动创建并接受任意版本。
- 名称只允许 `[A-Za-z0-9_.-]`；空名、非法字符、目标版本等于当前版本均抛 `ValueError`。
- 缓存口径：`put_cache` 按版本打标（缺省取当前版本），`clear_cache` 只清非目标版本条目。
- 审计：`audit_log()` 记录操作人、起止版本、原因、状态和每步结果，返回字典不含 `operator`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

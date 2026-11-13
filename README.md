# confdrift

现状：抓当前配置存快照，只报告两边都有的键的值变化。

问题：新增和删除看不出，敏感项明文比对，不同环境互相覆盖。

目标：多环境漂移检测，四类差异分级，敏感项只比哈希，重复告警合并。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 指纹口径

- `snapshot(env, config, sensitive=[...])` 采集运行配置并生成 `fingerprint`：键排序后 JSON 序列化取 SHA-256，环境间可安全比较。
- 声明为 `sensitive` 的键以值哈希代入指纹，快照另存 `hashes`，明文不出现在指纹和差异输出中。
- `diff(base, target)` 先比 `fingerprint_match`，再分四类差异：`added` / `removed` / `changed` / `type_changed`。
- 分级：added=low，changed=medium，removed/type_changed=high，敏感项漂移=critical；整体 `severity` 取最高。

## 修复口径

- 每条差异带 `source`（base_only / target_only / both）和 `suggestion`：以仓库基线为准回滚，或评审后把环境新值回写基线。
- 新增键：有意则回写基线，否则从环境移除；删除键：补回环境或确认下线后删基线。
- 类型变化：统一两侧类型后再比值；敏感项只比哈希、展示为 `***`，按密钥轮换流程核对。
- `merge_alerts(*reports)` 按 env+key+kind 合并重复漂移告警并计数，避免同一漂移反复告警。

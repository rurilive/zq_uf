# datacontract

数据契约双向兼容校验，纯 Python 标准库。

## 契约格式

```python
{"version": 1, "fields": [...], "types": {...},
 "required": [...], "defaults": {...}, "enums": {...}}
```

## 兼容规则

- 方向：backward = 新契约读旧数据，forward = 旧契约读新数据。
- 破坏项（breaking）：删除列（forward）、类型收紧如 float→int（backward）。
- 警告项（warning）：默认值变化、枚举收缩、必填变化、新增必填列、类型放宽。
- 新增可选列完全兼容；未知类型对的变更按 warning 处理。
- 每个问题项给出 direction、field、severity 和修复建议；重复校验结果一致。

## 用法

```python
from datacontract import check, save, load
result = check(old, new)          # compatible / breaking / suggestions
save(contract, "v1.json")         # 归档
contract = load("v1.json")        # 回读
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```


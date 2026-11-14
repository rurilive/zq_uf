# jsonshape

纯 Python 标准库的响应结构巡检模块。按站点和字段路径生成结构指纹，
每个站点在最近窗口内独立维护基线，对字段增删、类型变化和缺失率突升
分别告警；冷启动只学习不告警，连续多批异常自动升级。

## 结构指纹

extract_fingerprint(payload) 递归遍历响应，为每个字段路径记录：

- 路径：$ 为根，字典按 key 下沉，数组元素统一记为 []，如 $.items.[].sku
- 类型：Python 类型名（dict/list/str/int/float/bool/NoneType 等）
- 数组长度分布：该路径在一批样本中各长度出现次数（指纹的 array_len 字段）

一批响应（observe 接受单个 dict 或 dict 列表）按路径聚合成基线统计：
主导类型、类型计数、数组长度分布、出现页数与缺失率。

## 基线与阈值

每个站点各自维护状态，互不影响（jsonshape/shape.py 顶部可调）：

- MIN_PAGES = 5：累计样本页少于 5 页时处于冷启动，只学习不告警
- MIN_BATCHES = 3：历史批次少于 2 批时同样处于冷启动
- WINDOW_BATCHES = 20：基线只取最近 20 批，老结构自动淘汰
- ESCALATE_STREAK = 3：连续 3 批异常，severity 从 warning 升为 critical；出现一批正常即清零
- MISSING_RATE_DELTA = 0.3 与 MISSING_RATE_MIN = 0.2：当前缺失率比基线高 0.3 且自身不低于 0.2 才报缺失率突升
- SAMPLE_PAGES_LIMIT = 3：每条告警最多附 3 个异常样本页下标

## 告警类型

- field_added：基线没有、本批出现的路径（baseline 为 None）
- field_removed：基线有、本批全缺的路径（current 为 None）
- type_changed：同一路径主导类型与基线不一致
- missing_rate_spike：类型未变，但缺失率相对基线突升

每条告警带 site、path、baseline、current、sample_pages、
severity 和连续异常批次数 streak。

## 用法

    from jsonshape import observe, report, reset

    observe("shop", {"id": 1, "items": [{"sku": "a"}]})  # 冷启动：只学习
    observe("shop", [{"id": 2}, {"id": 3}])              # 也接受一批多页
    report("shop")  # {"alerts": [...]}；report() 返回全部站点
    reset()         # 清空基线和告警

## 运行

    python3 demo.py
    python3 -m unittest discover -s tests

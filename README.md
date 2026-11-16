# forensics

安全方向的二进制符号恢复小工具，纯标准库。

去掉符号的二进制里函数全是 `sub_<地址>`，换版本地址就变。本工具按三类
信号给函数匹配公开符号，并为每个结论留下可回溯的依据和可信程度：

- **已知库特征**：函数体内的魔数（如 MD5/SHA1 初始化常量）、特征字符串、
  调用特征，数据在 `symrecover/catalog.py`，可用 `load_catalog()` 追加；
- **公开符号表 / 导入表**：权威映射，直接给 high；
- **调用关系**：被调函数恢复后按调用特征定点传播，结构性推断给 low，
  无法唯一定位的保持未恢复，不硬猜。

同一函数在不同版本里靠内容特征（调用结构、字符串、常量的颜色求精）生成
**与地址无关的稳定标识**，改名不换标识、改名过程留历史。匹配不上的保留
`sub_<地址>` 名字，按模块分组并给出数量，低可信结论可用 `pin()` 人工固定。

```python
from symrecover import recover, report, lookup, pin, identity, export, load_catalog

result = recover(binary)          # recovered/total/names/unmatched(_count/_by_module)
lookup("routerd", "0804a300")      # 单函数：name/confidence/evidence/stable_id/history
pin("routerd", "0804a600", "parse_config_line", note="对照 main.c:88")  # 人工固定
identity(stable_id)               # 当前名字 + 改名历史 + 是否人工固定
export("sym")                     # 地址+名字符号表，和公开符号表对照
export("id")                      # 稳定标识 <-> 各版本地址对照
report()                          # 可信分布、逐条依据、未恢复分组与总数
```

可信程度：`none`（未恢复）< `low`（调用关系传播）< `medium`（单条库特征/
调用特征）< `high`（魔数签名/符号表/多信号一致）< `manual`（人工固定）。
同级信号指向不同名字时判冲突、不自动命名，冲突双方都留在依据里交人工裁决。

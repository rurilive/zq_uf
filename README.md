# htmlcanon

采集页面的 DOM 级规范化与稳定指纹模块，纯 Python 标准库（`html.parser` + `hashlib`）。

## 用法

```python
from htmlcanon import canonical, same, analyze

canonical(html)          # 规范化文本（接口与旧版一致）
same(a, b)               # 两页面是否属于同一等价类（比较合并块指纹）
analyze(html)            # CanonResult：规范化文本 + 规则命中 + 块指纹 + 等价类
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 规范化规则

按顺序作用于 DOM，每条规则命中都记录在 `analyze().rules`（含规则号、标签、属性、原文字符区间）。

| 规则号 | 含义 |
| --- | --- |
| `tag-lowercase` | 标签名、属性名统一小写（解析器内置） |
| `attr-dropped` | 属性不在白名单（如 `onclick`、`style`、`data-*`），剔除 |
| `attr-sorted` | 保留属性按名排序，属性顺序不再影响结果 |
| `attr-boolean` | 布尔属性（`disabled`/`checked`/`hidden` 等）去值只留名 |
| `class-sorted` | `class` 去重并按词排序 |
| `whitespace-normalized` | 连续 HTML 空白（`\t\n\r\f `，不含 NBSP/全角空格）折叠为单空格；块级元素及块边界的首尾空白剥离，行内元素两侧的词边界空格保留 |
| `void-normalized` | 自闭合斜杠统一去除；空元素（`br`/`img`/`input` 等）不写闭合 |
| `drop-script` / `drop-style` | `script`、`style` 整节点剔除 |
| `drop-comment` / `drop-doctype` | 注释、doctype、声明、处理指令剔除 |
| `drop-hidden` | 隐藏节点剔除：`hidden` 属性、`input type=hidden`、`aria-hidden=true`、`style` 含 `display:none`/`visibility:hidden` |

属性白名单 = 全局集合（`id,class,title,lang,dir,tabindex,role,aria-*,name,value,href,src,alt,action,method,type,placeholder,for,colspan,rowspan,datetime`）并上各标签专属集合（见 `canon.py` 的 `GLOBAL_ATTRS`/`TAG_ATTRS`）。

序列化紧凑无多余空白，文本与属性值做 HTML 转义；元素相对顺序、文本内容保持不变（顺序是有意义的，不做重排）。

## 指纹与等价类口径

1. 规范化后按块级标签（`article/nav/div/p/h1…`，表见 `BLOCK_TAGS`）切内容块；骨架 `html/head/body` 不产生块，相邻行内内容并入匿名块（`tag="#text"`）。
2. 每个块对其规范化文本算 `SHA256`，得到**块指纹**；广告/导航等噪声块的变化只影响自己那一块，正文块指纹保持稳定，可据此定位差异来源。
3. 各块指纹按文档顺序逐行拼接再做一次 `SHA256`，得到**文档指纹**；`same()` 即比较文档指纹。
4. 文档指纹前 12 位作为**等价类编号**：同内容、不同写法（大小写/属性顺序/空白/自闭合/噪声）落入同一等价类；内容不同则编号不同。

## 按块回查原文

每个 `Block` 带 `start/end` 原文字符偏移：

```python
r = analyze(html)
start, end = r.source_of(0)          # 也可 r.blocks[0].start/.end
html[start:end]                      # 该块对应的原始 HTML 片段
r.explain()                          # 规则命中 + 每块指纹/位置的可读说明
```

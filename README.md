# xssreflect

按**回显位置与上下文**判断的反射型 / 存储型 XSS 检测工具。

旧版只做整段 payload 原文匹配，存在三个问题：编码回显被当成没命中、解码后
的文本被当成命中、同一页面给出互相矛盾的结论；存储型也没有入口，只能人工
逐个点。本版围绕「先定位上下文 → 还原编码 → 判断能否执行」重做。

## 检测思路

1. **canary 定位回显**：用唯一字母数字标记 `CANARY` 跟随 payload，标记本身
   不含特殊字符，无论服务端如何编码都能定位到回显。
2. **区分回显上下文**：内置状态机解析 HTML，把每处回显归入
   HTML 正文、属性、URL 属性、脚本、注释、标签名六类。
3. **按上下文还原编码**：属性/正文做 HTML 实体解码，URL 属性再做百分号
   解码，脚本字符串做 JS 反转义；每一步都记入 `restore_chain` 还原过程。
4. **按上下文判断可执行性**：
   - 正文：裸尖括号可注入标签 → vulnerable；尖括号被实体编码 → safe；
   - 属性：裸引号/空白能闭合或断开属性才 vulnerable；
   - URL：原文出现 `javascript:`/`data:text/html` 才可执行，编码后拼出的不算；
   - 脚本：裸 `</script>` 或未转义引号可跳出，字符串内被转义则 safe；
   - 注释：注入的注释结束符才可跳出；标签名回显直接可改写标记。
5. **拿不准的给 `manual`**（如样式块、DOCTYPE 内），不武断报漏洞也不漏报。
6. **同上下文、同结论的重复回显合并为一条**（带 `count` 和全部 `offsets`），
   同一参数在不同位置的回显逐处判断，不因一处命中跳过其余位置。

## 入口

| 函数 | 作用 |
| --- | --- |
| `load(page, body, content_type="text/html")` | 登记响应正文 |
| `check(url, param, payload=None, source="reflected", entry_id=None)` | 检测一个或一组参数，返回命中位置、上下文、payload、还原过程 |
| `submit(page, param, payload, entry_id=None)` | 登记存储型注入入口（提交点） |
| `report()` | 按页面汇总结论、未覆盖参数、解析失败、存储型入口状态 |
| `reset()` | 清空登记与结果（测试用） |

`check` 返回结构：

```python
{
  "page", "params", "source", "entry_id", "payload",
  "findings": [
    {"param", "offset", "offsets", "count", "context", "context_detail",
     "encoding", "echo", "restored", "restore_chain", "verdict", "reason"}
  ],
  "uncovered":    [{"param", "reason"}],      # 未覆盖参数及跳过原因
  "parse_errors": [{"offset", "reason"}],      # 页面结构变化导致的解析失败
}
```

- 未在响应中找到 canary 的参数进入 `uncovered` 并写明原因；
- 标签未闭合、注释未闭合、嵌套异常等结构问题进入 `parse_errors`，与命中结论分开列；
- `verdict` 取值 `vulnerable` / `safe` / `manual`，`manual` 留待人工确认。

## 运行

```bash
python3 demo.py                       # 端到端演示
python3 -m unittest discover -s tests # 33 个用例，覆盖六类上下文与各类编码
```

纯标准库实现，无第三方依赖。

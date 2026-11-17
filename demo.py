"""反射型 / 存储型 XSS 检测演示。

入口：
- load(page, body)            登记响应正文
- check(url, param)           按回显位置检测，返回上下文、payload、还原过程
- submit(page, param, payload) 登记存储型注入入口
- report()                    按页面汇总结论，含未覆盖参数与解析失败
"""

from xssreflect import CANARY, PAYLOAD, check, load, report, reset, submit

reset()

# 第一段：同一页面里既有可执行的裸回显，也有被实体编码的安全回显
body = (
    "<div>%s%s</div>"
    "<input value=\"%sabc&amp;\">"
    "<p>%s&lt;script&gt;alert(1)&lt;/script&gt;</p>"
) % (CANARY, PAYLOAD, CANARY, CANARY)
load("search", body)
out = check("search", "q")

print("== 反射型检测：search / q ==")
for f in out["findings"]:
    print("上下文=%-9s 结论=%-10s 编码=%-6s 位置=%s"
          % (f["context"], f["verdict"], f["encoding"], f["offsets"]))
    print("  依据:", f["reason"])
print("未覆盖参数:", out["uncovered"])
print("解析失败:", out["parse_errors"])

# 第二段：存储型——先登记提交入口，再到回显页面找 canary
print()
print("== 存储型检测：留言板 ==")
load("board", "<li>%s%s</li>" % (CANARY, PAYLOAD))
entry = submit("board", "comment", PAYLOAD)
stored = check("board", "comment", entry["payload"],
               source="stored", entry_id=entry["entry_id"])
print("入口:", entry["entry_id"], "结论:",
      [(f["context"], f["verdict"]) for f in stored["findings"]])

# 第三段：未覆盖参数与解析失败单列
print()
print("== 汇总报告 ==")
rep = report()
print("命中条数:", rep["hits"], "结论分布:", rep["verdict_counts"])
print("上下文分布:", rep["contexts"])
print("未覆盖:", rep["uncovered"])
print("解析失败数:", rep["parse_error_count"])
print("存储型入口:", rep["stored_entries"])

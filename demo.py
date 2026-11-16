"""演示：按回显上下文判断、编码还原、重复合并、存储型入口与按页报告。"""

import json

import xssreflect
from xssreflect import PAYLOAD

print("== 1. 同一页面，原样 vs 实体编码，两种结论都按上下文给 ==")
xssreflect.load("search",
                "<div>%s</div>"
                "<input value='%s'>"
                % (PAYLOAD,
                   PAYLOAD.replace("<", "&lt;").replace(">", "&gt;")))
out = xssreflect.check("search", "q")
for f in out["findings"]:
    print("上下文=%-9s 结论=%-6s 位置=%s 还原=%s"
          % (f["context"], f["verdict"], f["offset"],
             [s["encoding"] for s in f["restore"]] or "无需还原"))

print("\n== 2. 编码先还原再判断，还原过程随结论返回 ==")
xssreflect.load("escaped",
                "<div>%253Cscript%253Ealert(1)%253C/script%253E</div>")
f = xssreflect.check("escaped", "q")["findings"][0]
print("结论=%s，还原后=%r，共 %d 步：" % (f["verdict"], f["decoded"],
                                       len(f["restore"])))
for step in f["restore"]:
    print("  %s: %r -> %r" % (step["encoding"], step["from"], step["to"]))

print("\n== 3. 同页重复回显合并成一条，记录全部偏移 ==")
xssreflect.load("double", "%s and %s" % (PAYLOAD, PAYLOAD))
f = xssreflect.check("double", "q")["findings"][0]
print("合并后条数=1，出现次数=%d，偏移列表=%s"
      % (f["occurrences"], f["offsets"]))

print("\n== 4. 存储型入口：先登记入口，再对页面正文复检 ==")
xssreflect.add_entry("profile", "bio", stored=True)
xssreflect.load("profile", "<div>%s</div>" % PAYLOAD)
stored = xssreflect.check("profile", "bio")["findings"][0]
print("存储型回显 上下文=%s 结论=%s" % (stored["context"], stored["verdict"]))

print("\n== 5. 未覆盖参数、跳过原因与解析失败单列 ==")
xssreflect.add_entry("admin", "token", reason="需要登录态，无法发送探测请求")
xssreflect.load("admin", "<div><input value='%s" % PAYLOAD)
broken = xssreflect.check("admin", "token")
print("未覆盖:", json.dumps(broken["uncovered"], ensure_ascii=False))
print("解析失败:", [e["reason"] for e in broken["parse_errors"]])

print("\n== 6. check_all + report，运营按页面查看结论 ==")
xssreflect.check_all()
print(json.dumps(xssreflect.report(), ensure_ascii=False, indent=2))

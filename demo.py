from symrecover import export, identity, lookup, pin, recover, report

# 1.0 版本：地址 0804axxx，带魔数、字符串、调用关系和导入桩
V1 = {
    "name": "routerd",
    "version": "1.0",
    "functions": [
        {"address": "0804a100", "module": "crypto", "calls": ["0804b000"],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE",
                       "0x10325476", "0xC3D2E1F0"]},
        {"address": "0804a200", "module": "crypto", "calls": ["0804b000"],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE", "0x10325476"]},
        {"address": "0804a300", "module": "auth", "calls": [],
         "strings": ["admin", "login failed"]},
        {"address": "0804a400", "module": "auth", "calls": ["0804b030"],
         "strings": ["/tmp/key"]},
        {"address": "0804a500", "module": "auth", "calls": ["0804a200"]},
        {"address": "0804a600", "module": "parser", "calls": ["0804a300"]},
        {"address": "0804b000", "module": "libc_wrap", "calls": [], "imports": ["memset"]},
        {"address": "0804b030", "module": "libc_wrap", "calls": [], "imports": ["memcpy"]},
    ],
}

# 1.1 版本：同样的函数，地址整体漂移到 0804cxxx，结构和字符串不变
V2 = {
    "name": "routerd",
    "version": "1.1",
    "functions": [
        {"address": "0804c100", "module": "crypto", "calls": ["0804d000"],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE",
                       "0x10325476", "0xC3D2E1F0"]},
        {"address": "0804c200", "module": "crypto", "calls": ["0804d000"],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE", "0x10325476"]},
        {"address": "0804c300", "module": "auth", "calls": [],
         "strings": ["admin", "login failed"]},
        {"address": "0804c400", "module": "auth", "calls": ["0804d030"],
         "strings": ["/tmp/key"]},
        {"address": "0804c500", "module": "auth", "calls": ["0804c200"]},
        {"address": "0804c600", "module": "parser", "calls": ["0804c300"]},
        {"address": "0804d000", "module": "libc_wrap", "calls": [], "imports": ["memset"]},
        {"address": "0804d030", "module": "libc_wrap", "calls": [], "imports": ["memcpy"]},
    ],
}

# 第一段：带证据的恢复，分析同学按函数看来源和可信程度
r1 = recover(V1)
print("== 1.0 恢复 %d/%d，未恢复 %d ==" % (r1["recovered"], r1["total"], r1["unmatched_count"]))
for item in r1["names"]:
    print(item["address"], item["name"], item["confidence"], item["stable_id"])
print("admin_login 的依据：")
for ev in lookup("routerd", "0804a300")["evidence"]:
    print("  -", ev["signal"], ev["detail"])
print(report())

# 第二段：换版本地址漂移，稳定标识不变；低可信人工固定；导出与改名历史
recover(V2)
old_id = lookup("routerd", "0804a500")["stable_id"]
new_id = lookup("routerd", "0804c500")["stable_id"]
print("\n== 跨版本同一函数标识一致：", old_id == new_id, old_id)

pin("routerd", "0804a600", "parse_config_line", note="对照开源版 main.c:88 确认")
pinned = lookup("routerd", "0804a600")
print("人工固定后：", pinned["name"], pinned["confidence"])
print("该函数改名历史：", [(h["name"], h["source"]) for h in identity(pinned["stable_id"])["history"]])

print("\n符号表导出（可直接和公开符号表对照）：")
print(export("sym", binary="routerd"))
print("\n未恢复地址按模块分组：", report()["unmatched"])
print("未恢复总数：", report()["unmatched_count"])

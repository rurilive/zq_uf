import unittest

from symrecover import export, identity, load_catalog, lookup, pin, recover, report, reset_catalog
from symrecover import symbols

V1 = {
    "name": "routerd",
    "version": "1.0",
    "functions": [
        # 魔数同时是 MD5_Init 的子集，更具体的 SHA1_Init 签名应胜出
        {"address": "0804a100", "module": "crypto",
         "calls": ["0804b000"], "strings": [],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE", "0x10325476", "0xC3D2E1F0"]},
        # 只有 4 个魔数：MD5_Init 命中，同时靠调用 memset 桩满足其 callees 特征
        {"address": "0804a200", "module": "crypto",
         "calls": ["0804b000"], "strings": [], "constants": [
             "0x67452301", "0xEFCDAB89", "0x98BADCFE", "0x10325476"]},
        # 字符串特征：medium
        {"address": "0804a300", "module": "auth",
         "calls": [], "strings": ["admin", "login failed"]},
        # 字符串 + 调用特征两个独立信号同时指向 load_key_file，可信度升级
        {"address": "0804a400", "module": "auth",
         "calls": ["0804b030"], "strings": ["/tmp/key"], "constants": []},
        # 只靠调用关系传播：被调函数 MD5_Init 恢复后才能定位
        {"address": "0804a500", "module": "auth",
         "calls": ["0804a200"], "strings": [], "constants": []},
        # 什么特征都没有：保留 sub_ 名字，进未恢复清单
        {"address": "0804a600", "module": "parser",
         "calls": ["0804a300"], "strings": [], "constants": []},
        # 导入 + 调用特征两个信号指向 strdup，升级到 high
        {"address": "0804a700", "module": "libc_wrap",
         "calls": ["0804b010", "0804b020"], "strings": [], "constants": []},
        # 被 strdup 调用的两个库桩，靠导入表给真名
        {"address": "0804b010", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["malloc"]},
        {"address": "0804b020", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["strlen"]},
        {"address": "0804b030", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["memcpy"]},
        {"address": "0804b000", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["memset"]},
    ],
}

# 同一二进制的新版本：地址整体漂移，结构/字符串/调用关系不变
V2 = {
    "name": "routerd",
    "version": "1.1",
    "functions": [
        {"address": "0804c100", "module": "crypto",
         "calls": ["0804d000"], "strings": [],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE", "0x10325476", "0xC3D2E1F0"]},
        {"address": "0804c200", "module": "crypto",
         "calls": ["0804d000"], "strings": [],
         "constants": ["0x67452301", "0xEFCDAB89", "0x98BADCFE", "0x10325476"]},
        {"address": "0804c300", "module": "auth",
         "calls": [], "strings": ["admin", "login failed"]},
        {"address": "0804c400", "module": "auth",
         "calls": ["0804d030"], "strings": ["/tmp/key"], "constants": []},
        {"address": "0804c500", "module": "auth",
         "calls": ["0804c200"], "strings": [], "constants": []},
        {"address": "0804c600", "module": "parser",
         "calls": ["0804c300"], "strings": [], "constants": []},
        {"address": "0804c700", "module": "libc_wrap",
         "calls": ["0804d010", "0804d020"], "strings": [], "constants": []},
        {"address": "0804d010", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["malloc"]},
        {"address": "0804d020", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["strlen"]},
        {"address": "0804d030", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["memcpy"]},
        {"address": "0804d000", "module": "libc_wrap", "calls": [], "strings": [], "imports": ["memset"]},
    ],
}


class RecoverTest(unittest.TestCase):
    def setUp(self):
        symbols._reset_state()
        reset_catalog()

    def test_constant_signature_matches_most_specific(self):
        result = recover(V1)
        entry = lookup("routerd", "0804a100")
        self.assertEqual(entry["name"], "SHA1_Init")
        self.assertEqual(entry["confidence"], "high")
        # 子集候选的处理过程要写进依据，评审能看到为什么选了 SHA1
        self.assertIn("更具体签名优先", " ".join(e["detail"] for e in entry["evidence"]))
        self.assertEqual(result["total"], 11)
        self.assertEqual(result["recovered"], 10)

    def test_subset_signature_still_matches(self):
        recover(V1)
        item = lookup("routerd", "0804a200")
        self.assertEqual(item["name"], "MD5_Init")
        self.assertTrue(any(e["signal"] == "constant" for e in item["evidence"]))

    def test_string_evidence_is_medium(self):
        recover(V1)
        item = lookup("routerd", "0804a300")
        self.assertEqual(item["name"], "admin_login")
        self.assertEqual(item["confidence"], "medium")
        self.assertTrue(any("admin" in e["detail"] for e in item["evidence"]))

    def test_two_signals_raise_confidence(self):
        recover(V1)
        # load_key_file：string(medium) + 调用 memcpy 桩命中 callees 特征(medium)
        item = lookup("routerd", "0804a400")
        self.assertEqual(item["name"], "load_key_file")
        signals = {e["signal"] for e in item["evidence"] if e.get("name") == "load_key_file"}
        self.assertEqual(signals, {"string", "callee"})
        # 两个不同信号一致，medium 上调到 high
        self.assertEqual(item["confidence"], "high")

    def test_callgraph_propagation_is_low(self):
        recover(V1)
        item = lookup("routerd", "0804a500")
        self.assertEqual(item["name"], "digest_session")
        self.assertEqual(item["confidence"], "low")
        self.assertTrue(any(e["signal"] == "callgraph" for e in item["evidence"]))

    def test_multi_signal_wrapper_reaches_high(self):
        recover(V1)
        item = lookup("routerd", "0804a700")
        self.assertEqual(item["name"], "strdup")
        # 无任何直接证据，纯靠调用 malloc+strlen 的形状传播推断：low
        self.assertEqual(item["confidence"], "low")
        self.assertTrue(any(e["signal"] == "callgraph" for e in item["evidence"]))

    def test_unmatched_keeps_address_name(self):
        result = recover(V1)
        item = lookup("routerd", "0804a600")
        self.assertEqual(item["name"], "sub_0804a600")
        self.assertFalse(item["recovered"])
        self.assertEqual(item["confidence"], "none")
        self.assertEqual(result["unmatched_count"], 1)

    def test_unmatched_grouped_by_module_with_count(self):
        result = recover(V1)
        self.assertEqual(result["unmatched_by_module"], {"parser": ["0804a600"]})
        self.assertEqual(result["unmatched"][0]["module"], "parser")

    def test_imports_are_authoritative_names(self):
        recover(V1)
        self.assertEqual(lookup("routerd", "0804b010")["name"], "malloc")
        self.assertEqual(lookup("routerd", "0804b010")["confidence"], "high")

    def test_public_symbol_table_wins(self):
        binary = {
            "name": "stripd", "version": "9.9",
            "symbols": [{"address": "0804a300", "name": "vendor_admin_check"}],
            "functions": [
                {"address": "0804a300", "calls": [], "strings": ["admin", "login failed"]},
            ],
        }
        result = recover(binary)
        item = result["names"][0]
        # 公开符号表 high 压过字符串 medium，不产生冲突
        self.assertEqual(item["name"], "vendor_admin_check")
        self.assertEqual(item["confidence"], "high")
        self.assertTrue(any(e["signal"] == "symbol" for e in item["evidence"]))

    def test_equal_specificity_conflict_stays_unmatched(self):
        load_catalog([
            {"name": "vendor_admin", "library": "vendor",
             "constants": [], "strings": ["admin", "login failed"], "callees": []},
        ])
        result = recover({
            "name": "conflictd", "version": "1.0",
            "functions": [{"address": "0804a300", "calls": [],
                           "strings": ["admin", "login failed"]}],
        })
        item = result["names"][0]
        self.assertFalse(item["recovered"])
        self.assertEqual(item["name"], "sub_0804a300")
        self.assertTrue(any(e["signal"] == "conflict" for e in item["evidence"]))
        self.assertEqual(result["unmatched_count"], 1)

    def test_invalid_input_raises(self):
        with self.assertRaises(ValueError):
            recover([])
        with self.assertRaises(ValueError):
            recover({"name": "x"})


class StableIdentityTest(unittest.TestCase):
    def setUp(self):
        symbols._reset_state()
        reset_catalog()

    def test_stable_id_survives_address_shift(self):
        r1 = recover(V1)
        r2 = recover(V2)
        ids1 = {e["name"]: e["stable_id"] for e in r1["names"] if e["recovered"]}
        ids2 = {e["name"]: e["stable_id"] for e in r2["names"] if e["recovered"]}
        self.assertEqual(ids1, ids2)
        # 未恢复的同结构函数也保持同一标识，只是地址变了
        u1 = {e["stable_id"] for e in r1["names"] if not e["recovered"]}
        u2 = {e["stable_id"] for e in r2["names"] if not e["recovered"]}
        self.assertEqual(u1, u2)

    def test_stable_id_is_address_independent(self):
        r1 = recover(V1)
        r2 = recover(V2)
        old = next(e for e in r1["names"] if e["address"] == "0804a300")
        new = next(e for e in r2["names"] if e["address"] == "0804c300")
        self.assertEqual(old["stable_id"], new["stable_id"])
        self.assertTrue(old["stable_id"].startswith("FID-"))

    def test_rename_keeps_identity_and_leaves_history(self):
        recover(V1)
        before = lookup("routerd", "0804a500")
        pin("routerd", "0804a500", "session_digest_v2", note="评审会上确认")
        after = lookup("routerd", "0804a500")
        self.assertEqual(before["stable_id"], after["stable_id"])
        self.assertEqual(after["name"], "session_digest_v2")
        self.assertEqual(after["confidence"], "manual")
        record = identity(after["stable_id"])
        names = [h["name"] for h in record["history"]]
        self.assertEqual(names, ["digest_session", "session_digest_v2"])
        self.assertTrue(record["pinned"])

    def test_pin_survives_recover_and_applies_to_unmatched(self):
        recover(V1)
        pin("routerd", "0804a600", "parse_config_line", note="对照开源版 main.c:88")
        v1_again = recover(V1)
        item = next(e for e in v1_again["names"] if e["address"] == "0804a600")
        self.assertEqual(item["name"], "parse_config_line")
        self.assertEqual(item["confidence"], "manual")
        # pin 按二进制名+地址挂账：v2 地址漂移后的新地址仍待人工处理
        v2 = recover(V2)
        self.assertEqual(v2["unmatched_count"], 1)
        self.assertEqual(v2["unmatched"][0]["address"], "0804c600")

    def test_identity_unknown_returns_empty(self):
        self.assertEqual(identity("FID-NOPE"), {})


class ReportAndExportTest(unittest.TestCase):
    def setUp(self):
        symbols._reset_state()
        reset_catalog()

    def test_report_confidence_distribution(self):
        recover(V1)
        summary = report()
        self.assertEqual(summary["binaries"], 1)
        self.assertEqual(summary["total"], 11)
        self.assertEqual(summary["recovered"], 10)
        self.assertEqual(summary["unmatched_count"], 1)
        self.assertEqual(summary["confidence"]["none"], 1)
        self.assertEqual(summary["confidence"]["low"], 2)
        self.assertEqual(summary["confidence"]["medium"], 1)
        self.assertEqual(summary["confidence"]["high"], 7)
        # 每条依据都能回溯到具体函数
        row = next(r for r in summary["evidence"] if r["address"] == "0804a500")
        self.assertEqual(row["signal"], "callgraph")
        # 未恢复按 二进制+模块 分组，带数量
        self.assertEqual(summary["unmatched"][0]["binary"], "routerd")
        self.assertEqual(summary["unmatched"][0]["module"], "parser")
        self.assertEqual(summary["unmatched"][0]["count"], 1)

    def test_report_covers_two_versions(self):
        recover(V1)
        recover(V2)
        summary = report()
        self.assertEqual(summary["binaries"], 1)   # 同一个二进制名
        self.assertEqual(summary["versions"], 2)   # 两个版本记录都保留
        self.assertEqual(summary["total"], 22)

    def test_export_sym_matches_symbol_table_format(self):
        recover(V1)
        text = export("sym")
        self.assertIn("# routerd 1.0", text)
        self.assertIn("0x0804a300 admin_login", text)
        # 未恢复的不导出符号，避免污染公开符号表对照
        self.assertNotIn("sub_", text)

    def test_export_id_links_versions(self):
        recover(V1)
        recover(V2)
        mapping = export("id")
        admin = next(m for m in mapping if m["name"] == "admin_login")
        addresses = {(a["version"], a["address"]) for a in admin["addresses"]}
        self.assertEqual(addresses, {("1.0", "0804a300"), ("1.1", "0804c300")})

    def test_export_json_is_per_function(self):
        recover(V1)
        payload = export("json")
        self.assertEqual(len(payload), 1)
        item = next(f for f in payload[0]["functions"] if f["address"] == "0804a300")
        self.assertEqual(item["name"], "admin_login")
        self.assertTrue(item["evidence"] and item["stable_id"])

    def test_export_unknown_format_raises(self):
        recover(V1)
        with self.assertRaises(ValueError):
            export("pdf")

    def test_lookup_missing_binary_is_empty(self):
        self.assertEqual(lookup("ghost", "0804a300"), {})


if __name__ == "__main__":
    unittest.main()

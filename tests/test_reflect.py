import unittest

from xssreflect import CANARY, PAYLOAD, check, load, report, reset, submit


class ContextAwareReflectTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- HTML 正文 -------------------------------------------------------

    def test_raw_echo_in_html_body_is_vulnerable(self):
        load("search", "<div>%s%s</div>" % (CANARY, PAYLOAD))
        out = check("search", "q")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["context"], "html")
        self.assertEqual(f["verdict"], "vulnerable")
        self.assertEqual(f["offsets"], [5])

    def test_entity_encoded_echo_is_safe_not_missed(self):
        # 旧实现：编码回显直接漏报。新实现：命中并判 safe，还原链可见
        load("safe", "<div>%s&lt;script&gt;alert(1)&lt;/script&gt;</div>" % CANARY)
        out = check("safe", "q")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["context"], "html")
        self.assertEqual(f["verdict"], "safe")
        self.assertEqual(f["encoding"], "entity")
        self.assertIn("<script>alert(1)</script>", f["restored"])
        layers = [step["layer"] for step in f["restore_chain"]]
        self.assertEqual(layers, ["html_entity"])

    def test_same_page_raw_and_encoded_get_distinct_verdicts(self):
        # 同一页面两处回显：一处裸尖括号可执行，一处实体编码安全
        body = "<p>%s%s</p><p>%s%s</p>" % (
            CANARY, PAYLOAD, CANARY,
            "&lt;script&gt;alert(1)&lt;/script&gt;")
        load("mixed", body)
        out = check("mixed", "q")
        verdicts = sorted(f["verdict"] for f in out["findings"])
        self.assertEqual(verdicts, ["safe", "vulnerable"])

    # ---- 属性 ------------------------------------------------------------

    def test_quoted_attribute_encoded_quote_is_safe(self):
        load("attr", '<input value="%sabc&amp;">' % CANARY)
        out = check("attr", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "attribute")
        self.assertEqual(f["verdict"], "safe")

    def test_attribute_broken_out_with_raw_quote_is_vulnerable(self):
        load("break", '<input value="%s"><script>alert(1)</script>">' % CANARY)
        out = check("break", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "attribute")
        self.assertEqual(f["verdict"], "vulnerable")

    def test_unquoted_attribute_whitespace_breaks_out(self):
        load("unquoted", '<input value=%s onload=alert(1)>' % CANARY)
        out = check("unquoted", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "attribute")
        self.assertEqual(f["verdict"], "vulnerable")

    # ---- URL -------------------------------------------------------------

    def test_href_javascript_scheme_is_vulnerable(self):
        load("href", '<a href="%sjavascript:alert(1)">x</a>' % CANARY)
        out = check("href", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "url")
        self.assertEqual(f["verdict"], "vulnerable")

    def test_href_percent_encoded_scheme_is_safe(self):
        body = '<a href="%s%%6a%%61vascript:alert(1)">x</a>' % CANARY
        load("href2", body)
        out = check("href2", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "url")
        self.assertEqual(f["verdict"], "safe")
        layers = [step["layer"] for step in f["restore_chain"]]
        self.assertEqual(layers, ["html_entity", "percent"])

    # ---- 脚本 ------------------------------------------------------------

    def test_script_string_raw_quote_breaks_out(self):
        load("js", "<script>var a = '%s';alert(1);</script>" % CANARY)
        out = check("js", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "script")
        self.assertTrue(f["context_detail"]["string"]["in_string"])
        self.assertEqual(f["verdict"], "vulnerable")

    def test_script_string_escaped_quote_is_safe(self):
        load("js2", r"<script>var a = '%s\';alert(1);</script>" % CANARY)
        out = check("js2", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "script")
        self.assertEqual(f["verdict"], "safe")

    def test_script_close_tag_is_vulnerable(self):
        load("js3", "<script>var a = '%s</script><img src=x>';</script>" % CANARY)
        out = check("js3", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "script")
        self.assertEqual(f["verdict"], "vulnerable")

    # ---- 注释与标签名 ----------------------------------------------------

    def test_comment_without_close_sequence_is_safe(self):
        load("comment", "<!-- %s hello -->" % CANARY)
        out = check("comment", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "comment")
        self.assertEqual(f["verdict"], "safe")

    def test_comment_breakout_is_vulnerable(self):
        load("comment2", "<!-- %s --><script>alert(1)</script>" % CANARY)
        out = check("comment2", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "comment")
        self.assertEqual(f["verdict"], "vulnerable")

    # ---- 合并与逐处判断 --------------------------------------------------

    def test_repeated_same_context_echoes_merged(self):
        load("double", "%s%s and %s%s" % (CANARY, PAYLOAD, CANARY, PAYLOAD))
        out = check("double", "q")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["count"], 2)
        self.assertEqual(len(f["offsets"]), 2)

    def test_same_param_multiple_positions_all_judged(self):
        body = ('<div>%s%s</div><input value="%s123">'
                % (CANARY, PAYLOAD, CANARY))
        load("multi", body)
        out = check("multi", "q")
        contexts = sorted(f["context"] for f in out["findings"])
        self.assertEqual(contexts, ["attribute", "html"])
        self.assertTrue(any(f["verdict"] == "vulnerable"
                            for f in out["findings"]))

    # ---- 未覆盖参数 ------------------------------------------------------

    def test_uncovered_param_has_reason(self):
        load("p", "<div>hello</div>")
        out = check("p", ["q", "page"])
        self.assertEqual(out["findings"], [])
        self.assertEqual(
            [item["param"] for item in out["uncovered"]], ["q", "page"])
        self.assertIn("canary", out["uncovered"][0]["reason"])

    # ---- 解析失败单列 ----------------------------------------------------

    def test_parse_error_listed_separately(self):
        load("broken", "<div>%s%s" % (CANARY, PAYLOAD))  # 标签未闭合
        out = check("broken", "q")
        self.assertTrue(out["parse_errors"])
        self.assertIn("未闭合", out["parse_errors"][0]["reason"])
        # 解析失败不影响回显本身的判断
        self.assertTrue(out["findings"])

    # ---- 存储型入口 ------------------------------------------------------

    def test_stored_entry_observed_and_pending(self):
        load("board", "<li>%s留言</li>" % CANARY)
        entry = submit("board", "comment", PAYLOAD)
        self.assertTrue(entry["entry_id"])
        out = check("board", "comment", entry["payload"],
                    source="stored", entry_id=entry["entry_id"])
        self.assertEqual(out["source"], "stored")
        self.assertTrue(out["findings"])
        rep = report()
        self.assertEqual(rep["stored_entries"]["observed"], 1)

    def test_stored_entry_without_echo_stays_pending(self):
        load("quiet", "<html><body>nothing</body></html>")
        entry = submit("quiet", "comment", PAYLOAD)
        check("quiet", "comment", entry["payload"],
              source="stored", entry_id=entry["entry_id"])
        rep = report()
        self.assertEqual(rep["stored_entries"]["observed"], 0)
        self.assertEqual(rep["stored_entries"]["pending"][0]["entry_id"],
                         entry["entry_id"])

    # ---- 汇总 ------------------------------------------------------------

    def test_report_groups_by_page_and_context(self):
        load("a", "<div>%s%s</div>" % (CANARY, PAYLOAD))
        check("a", "q")
        rep = report()
        self.assertIn("a", rep["pages"])
        self.assertEqual(rep["pages"]["a"]["verdict"], "vulnerable")
        self.assertEqual(rep["verdict_counts"]["vulnerable"], 1)
        self.assertEqual(rep["contexts"].get("html"), 1)
        self.assertEqual(rep["parse_error_count"], 0)

    # ---- 入参校验 --------------------------------------------------------

    def test_reject_unknown_page(self):
        with self.assertRaises(ValueError):
            check("missing", "q")

    def test_stored_check_requires_entry_id(self):
        load("x", "<div>%s</div>" % CANARY)
        with self.assertRaises(ValueError):
            check("x", "q", source="stored")

    # ---- 边界与真实编码形态 ----------------------------------------------

    def test_numeric_entity_encoding_is_safe(self):
        # 数字实体 &#60; 与命名实体 &lt; 同等处理
        load("num", "<div>%s&#60;script&#62;x&#60;/script&#62;</div>" % CANARY)
        out = check("num", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "html")
        self.assertEqual(f["verdict"], "safe")
        self.assertEqual(f["encoding"], "entity")

    def test_hex_numeric_entity(self):
        load("hex", "<div>%s&#x3c;img src=x onerror=alert(1)&#x3e;</div>" % CANARY)
        out = check("hex", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "safe")

    def test_double_encoded_entity_is_safe(self):
        # 双重编码 &amp;lt; 解码一次仍是 &lt; 文本，不能执行
        load("dbl", "<div>%s&amp;lt;script&amp;gt;x&amp;lt;/script&amp;gt;</div>" % CANARY)
        out = check("dbl", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "safe")
        self.assertNotIn("<script>", f["restored"].replace("&lt;script&gt;", ""))

    def test_href_plain_url_is_safe(self):
        load("plain", '<a href="%s/search?q=1">x</a>' % CANARY)
        out = check("plain", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "url")
        self.assertEqual(f["verdict"], "safe")

    def test_data_html_scheme_is_vulnerable(self):
        load("data", '<a href="%sdata:text/html,alert(1)">x</a>' % CANARY)
        out = check("data", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "vulnerable")

    def test_script_code_area_is_vulnerable(self):
        # 回显在字符串外的代码区，无需闭合即可随代码解析
        load("code", "<script>var %s = 1;</script>" % CANARY)
        out = check("code", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "script")
        self.assertEqual(f["verdict"], "vulnerable")

    def test_style_block_goes_manual(self):
        load("style", "<style>.%s { color: red; }</style>" % CANARY)
        out = check("style", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "manual")

    def test_non_html_content_skipped_with_reason(self):
        load("json", '{"q":"%s"}' % CANARY, content_type="application/json")
        out = check("json", "q")
        self.assertEqual(out["findings"], [])
        self.assertTrue(out["parse_errors"])
        self.assertIn("非 HTML", out["parse_errors"][0]["reason"])

    def test_three_repeated_echoes_merge_with_count(self):
        body = " ".join("%s%s" % (CANARY, PAYLOAD) for _ in range(3))
        load("triple", body)
        out = check("triple", "q")
        self.assertEqual(len(out["findings"]), 1)
        self.assertEqual(out["findings"][0]["count"], 3)
        self.assertEqual(len(out["findings"][0]["offsets"]), 3)

    def test_canary_in_tag_name_is_vulnerable(self):
        # canary 落在标签结构内（属性解析不到值区间时归为 tagname）
        load("tn", "<%s img>" % CANARY)
        out = check("tn", "q")
        contexts = {f["context"] for f in out["findings"]}
        self.assertIn("tagname", contexts)
        self.assertTrue(any(f["verdict"] == "vulnerable"
                            for f in out["findings"]))

    def test_report_carries_restore_chain(self):
        load("rc", "<div>%s&lt;b&gt;x&lt;/b&gt;</div>" % CANARY)
        check("rc", "q")
        out = check("rc", "q")
        f = out["findings"][0]
        self.assertTrue(f["restore_chain"])
        self.assertEqual(f["restore_chain"][0]["layer"], "html_entity")

    def test_structural_quote_with_sibling_attr_is_safe(self):
        # canary 在 alt 内完整闭合，同级 onerror 是页面结构，不能误报
        load("sibling", '<img src=x alt="%s" onerror=alert(1)>' % CANARY)
        out = check("sibling", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "attribute")
        self.assertEqual(f["verdict"], "safe")

    def test_injected_quote_terminating_tag_is_vulnerable(self):
        # 注入引号让标签提前闭合，后面紧跟新元素
        load("break2", '<input value="%s"><b>x</b>">' % CANARY)
        out = check("break2", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "vulnerable")

    def test_wellformed_value_only_canary_is_safe(self):
        load("wellformed", '<input value="%s">' % CANARY)
        out = check("wellformed", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "safe")

    def test_unicode_escape_in_js_string_is_safe(self):
        # < 是 JS 字符串内的转义，HTML 解析器看不到真实 </script>
        load("uesc", r"<script>var a='%s<script>';</script>" % CANARY)
        out = check("uesc", "q")
        f = out["findings"][0]
        self.assertEqual(f["context"], "script")
        self.assertEqual(f["verdict"], "safe")

    def test_percent_encoding_in_html_body_is_safe(self):
        # 百分号编码只在 URL 上下文解码，正文里是普通文本
        load("pctbody", "<div>%s%%3Cscript%%3E</div>" % CANARY)
        out = check("pctbody", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], "safe")


if __name__ == "__main__":
    unittest.main()

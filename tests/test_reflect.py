import unittest

import xssreflect
from xssreflect import (
    PAYLOAD,
    CONTEXT_HTML,
    CONTEXT_ATTR,
    CONTEXT_SCRIPT,
    CONTEXT_URL,
    VERDICT_HIT,
    VERDICT_SAFE,
    VERDICT_REVIEW,
    ReflectEngine,
    add_entry,
    check,
    check_all,
    load,
    remove_entry,
    report,
    reset,
    unload,
)


class BaseCase(unittest.TestCase):
    def setUp(self):
        reset()


class HtmlContextTest(BaseCase):
    def test_raw_echo_in_body_is_hit(self):
        load("p", "<div>%s</div>" % PAYLOAD)
        out = check("p", "q")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["context"], CONTEXT_HTML)
        self.assertEqual(f["verdict"], VERDICT_HIT)
        self.assertEqual(f["offset"], 5)
        self.assertEqual(f["payload"], PAYLOAD)

    def test_entity_encoded_echo_is_safe_and_restored(self):
        load("p", "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")
        out = check("p", "q")
        f = out["findings"][0]
        # 编码后的回显不再被当成没命中，也不被当成命中，而是还原后判安全。
        self.assertEqual(f["verdict"], VERDICT_SAFE)
        self.assertEqual(f["decoded"], PAYLOAD)
        self.assertEqual([s["encoding"] for s in f["restore"]], ["html"])

    def test_percent_encoded_echo_is_safe(self):
        load("p", "<div>%3Cscript%3Ealert(1)%3C/script%3E</div>")
        out = check("p", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], VERDICT_SAFE)
        self.assertEqual(f["decoded"], PAYLOAD)
        self.assertEqual([s["encoding"] for s in f["restore"]], ["url"])

    def test_double_percent_encoding_records_each_restore_step(self):
        load("p", "<div>%253Cscript%253Ealert(1)%253C/script%253E</div>")
        out = check("p", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], VERDICT_SAFE)
        self.assertEqual([s["encoding"] for s in f["restore"]],
                         ["url", "url"])
        self.assertEqual(f["decoded"], PAYLOAD)

    def test_mixed_entity_and_url_encoding(self):
        load("p", "<div>%3Cscript%3Ealert(1)&lt;/script&gt;</div>")
        out = check("p", "q")
        f = out["findings"][0]
        self.assertEqual(f["verdict"], VERDICT_SAFE)
        self.assertEqual([s["encoding"] for s in f["restore"]],
                         ["url", "html"])


class AttributeContextTest(BaseCase):
    def test_quoted_attribute_with_encoded_quote_is_safe(self):
        body = '<input value="%s">' % (
            PAYLOAD.replace("<", "&lt;").replace(">", "&gt;")
                   .replace('"', "&quot;"))
        load("p", body)
        f = check("p", "q")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_ATTR)
        self.assertEqual(f["attribute"], "value")
        self.assertEqual(f["verdict"], VERDICT_SAFE)

    def test_raw_quote_breaks_out_of_attribute(self):
        # 探针含与定界符相同的原样引号，可闭合 value 后追加事件处理属性。
        probe = "' onmouseover='alert(1)"
        body = "<input value='%s'>" % probe
        load("p", body)
        f = check("p", "q", payload=probe)["findings"][0]
        self.assertEqual(f["context"], CONTEXT_ATTR)
        self.assertEqual(f["verdict"], VERDICT_HIT)

    def test_unquoted_attribute_space_breaks_out(self):
        probe = "x onmouseover=alert(1)"
        load("p", "<input value=%s>" % probe)
        f = check("p", "q", payload=probe)["findings"][0]
        self.assertEqual(f["context"], CONTEXT_ATTR)
        self.assertEqual(f["verdict"], VERDICT_HIT)

    def test_unquoted_attribute_with_encoded_space_is_safe(self):
        probe = "x onmouseover=alert(1)"
        load("p", "<input value=%s>" % probe.replace(" ", "&#32;"))
        f = check("p", "q", payload=probe)["findings"][0]
        self.assertEqual(f["verdict"], VERDICT_SAFE)

    def test_event_handler_attribute_hit_and_safe(self):
        load("hit", '<a onclick=\'%s\'>x</a>' % PAYLOAD)
        self.assertEqual(check("hit", "q")["findings"][0]["verdict"], VERDICT_HIT)
        safe_body = '<a onclick="%s">x</a>' % (
            PAYLOAD.replace("<", "&lt;").replace(">", "&gt;")
                   .replace("(", "&#40;").replace(")", "&#41;")
                   .replace("'", "&#39;"))
        load("safe", safe_body)
        f = check("safe", "q")["findings"][0]
        self.assertEqual(f["attribute"], "onclick")
        self.assertEqual(f["verdict"], VERDICT_SAFE)


class UrlContextTest(BaseCase):
    def test_javascript_scheme_is_hit(self):
        load("p", '<a href="javascript:alert(1)">x</a>')
        f = check("p", "q", payload="javascript:alert(1)")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_URL)
        self.assertEqual(f["verdict"], VERDICT_HIT)

    def test_encoded_colon_blocks_scheme(self):
        load("p", '<a href="javascript%3Aalert(1)">x</a>')
        f = check("p", "q", payload="javascript:alert(1)")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_URL)
        self.assertEqual(f["verdict"], VERDICT_SAFE)

    def test_data_text_html_scheme_is_hit(self):
        load("p", '<a href="data:text/html,%s">x</a>' % PAYLOAD)
        self.assertEqual(check("p", "q")["findings"][0]["verdict"], VERDICT_HIT)

    def test_normal_navigation_scheme_is_safe(self):
        load("p", '<a href="https://example.com/%s">x</a>' % PAYLOAD)
        f = check("p", "q")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_URL)
        self.assertEqual(f["verdict"], VERDICT_SAFE)

    def test_unknown_scheme_goes_to_review(self):
        load("p", '<a href="somecustom:%s">x</a>' % PAYLOAD)
        self.assertEqual(check("p", "q")["findings"][0]["verdict"],
                         VERDICT_REVIEW)


class ScriptContextTest(BaseCase):
    def test_raw_closing_script_is_hit(self):
        load("p", "<script>var a = '%s';</script>" % PAYLOAD)
        f = check("p", "q")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_SCRIPT)
        self.assertEqual(f["verdict"], VERDICT_HIT)

    def test_backslash_escaped_script_is_safe(self):
        body = "<script>var a = '%s';</script>" % (
            PAYLOAD.replace("</", "<\\/").replace("'", "\\'"))
        load("p", body)
        f = check("p", "q")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_SCRIPT)
        self.assertEqual(f["verdict"], VERDICT_SAFE)
        self.assertEqual(f["decoded"], PAYLOAD)

    def test_js_unicode_escape_is_safe(self):
        body = ('<script>var a = '
                '"\\u003cscript\\u003ealert(1)\\u003c/script\\u003e"'
                ';</script>')
        load("p", body)
        f = check("p", "q")["findings"][0]
        self.assertEqual(f["context"], CONTEXT_SCRIPT)
        self.assertEqual(f["verdict"], VERDICT_SAFE)
        self.assertEqual(f["decoded"], PAYLOAD)


class ReviewVerdictTest(BaseCase):
    def test_unknown_url_scheme_needs_manual_review(self):
        load("p", '<a href="somecustom:%s">x</a>' % PAYLOAD)
        f = check("p", "q")["findings"][0]
        self.assertEqual(f["verdict"], VERDICT_REVIEW)
        self.assertTrue(f["reason"])

    def test_findings_always_carry_restore_process_and_reason(self):
        load("hit", "<div>%s</div>" % PAYLOAD)
        load("safe",
             "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")
        for page, expect_steps in (("hit", 0), ("safe", 1)):
            f = check(page, "q")["findings"][0]
            self.assertIn("restore", f)
            self.assertEqual(len(f["restore"]), expect_steps)
            self.assertTrue(f["reason"])
            self.assertIn(f["verdict"],
                          (VERDICT_HIT, VERDICT_SAFE, VERDICT_REVIEW))


class MergeAndMultiPositionTest(BaseCase):
    def test_duplicate_echoes_in_same_context_merge_to_one(self):
        load("p", "%s and %s" % (PAYLOAD, PAYLOAD))
        out = check("p", "q")
        self.assertEqual(len(out["findings"]), 1)
        f = out["findings"][0]
        self.assertEqual(f["occurrences"], 2)
        self.assertEqual(f["offsets"], [0, 30])

    def test_same_param_in_different_contexts_judged_separately(self):
        body = "<div>%s</div><input value='%s'>" % (
            PAYLOAD,
            PAYLOAD.replace("<", "&lt;").replace(">", "&gt;"))
        load("p", body)
        findings = check("p", "q")["findings"]
        contexts = {f["context"] for f in findings}
        self.assertEqual(contexts, {CONTEXT_HTML, CONTEXT_ATTR})
        by_ctx = {f["context"]: f["verdict"] for f in findings}
        self.assertEqual(by_ctx[CONTEXT_HTML], VERDICT_HIT)
        self.assertEqual(by_ctx[CONTEXT_ATTR], VERDICT_SAFE)
        # 一处命中不跳过其余位置。
        self.assertEqual(len(findings), 2)

    def test_echo_in_comment_is_safe_but_still_recorded(self):
        load("p", "<!-- %s -->" % PAYLOAD)
        f = check("p", "q")["findings"][0]
        self.assertTrue(f["in_comment"])
        self.assertEqual(f["verdict"], VERDICT_SAFE)


class ParseErrorAndCoverageTest(BaseCase):
    def test_broken_structure_still_detects_and_lists_parse_error(self):
        load("p", "<div><input value='%s" % PAYLOAD)
        out = check("p", "q")
        self.assertTrue(out["parse_errors"])
        self.assertTrue(out["findings"])  # 解析失败不丢反射点

    def test_unclosed_tag_reports_parse_error(self):
        # 页面在一个引号属性中途截断，标签与引号都没有闭合。
        load("p", "<input value=\"%s" % PAYLOAD)
        out = check("p", "q")
        self.assertTrue(
            any("未闭合" in e["reason"] for e in out["parse_errors"]))

    def test_uncovered_param_with_reason(self):
        add_entry("p", "q", reason="需要登录态，无法发送探测请求")
        load("p", "<div>hello</div>")
        out = check("p", "q")
        self.assertEqual(len(out["uncovered"]), 1)
        self.assertEqual(out["uncovered"][0]["reason"],
                         "需要登录态，无法发送探测请求")

    def test_no_reflection_is_not_an_error(self):
        load("p", "<div>nothing here</div>")
        out = check("p", "q")
        self.assertEqual(out["findings"], [])
        self.assertEqual(out["parse_errors"], [])


class StoredEntryTest(BaseCase):
    def test_stored_entry_is_checked_via_check_all(self):
        add_entry("profile", "bio", stored=True)
        load("profile", "<div>%s</div>" % PAYLOAD)
        result = check_all()
        self.assertEqual(len(result["findings"]), 1)
        f = result["findings"][0]
        self.assertEqual(f["page"], "profile")
        self.assertEqual(f["param"], "bio")
        self.assertEqual(f["verdict"], VERDICT_HIT)

    def test_check_all_aggregates_pages_and_uncovered(self):
        add_entry("search", "q")
        add_entry("profile", "bio", stored=True)
        add_entry("admin", "token", reason="需要登录态")
        load("search", "<div>%s</div>" % PAYLOAD)
        load("profile", "<div>%s</div>" % PAYLOAD)
        result = check_all()
        pages = {f["page"] for f in result["findings"]}
        self.assertEqual(pages, {"search", "profile"})
        self.assertEqual(
            [(u["page"], u["param"]) for u in result["uncovered"]],
            [("admin", "token")])

    def test_remove_entry_and_unload(self):
        add_entry("p", "q")
        self.assertTrue(remove_entry("p", "q"))
        load("p", "x")
        self.assertTrue(unload("p"))
        with self.assertRaises(ValueError):
            check("p", "q")


class ReportTest(BaseCase):
    def test_report_groups_by_page_and_context(self):
        add_entry("p", "q")
        load("p", "<div>%s</div>" % PAYLOAD)
        check_all()
        rep = report()
        self.assertEqual(rep["hits"], 1)
        self.assertEqual(rep["pages"], {"p": 1})
        self.assertEqual(rep["contexts"], {CONTEXT_HTML: 1})
        self.assertEqual(rep["verdicts"][VERDICT_HIT], 1)

    def test_report_counts_verdicts_and_uncovered(self):
        add_entry("hitpage", "q")
        add_entry("safepage", "q")
        add_entry("skippage", "q", reason="跳过")
        load("hitpage", "<div>%s</div>" % PAYLOAD)
        load("safepage",
             "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")
        check_all()
        rep = report()
        self.assertEqual(rep["hits"], 1)
        self.assertEqual(rep["safe"], 1)
        self.assertEqual(rep["uncovered"], 1)
        self.assertEqual(rep["uncovered_items"][0]["page"], "skippage")

    def test_report_parse_error_pages_listed_separately(self):
        add_entry("p", "q")
        load("p", "<div><input value='%s" % PAYLOAD)
        check_all()
        rep = report()
        self.assertIn("p", rep["parse_error_pages"])
        self.assertGreaterEqual(rep["parse_errors"], 1)


class ValidationAndEngineTest(BaseCase):
    def test_reject_unknown_page(self):
        with self.assertRaises(ValueError):
            check("missing", "q")

    def test_reject_empty_page_and_param(self):
        with self.assertRaises(ValueError):
            load("", "x")
        load("p", "x")
        with self.assertRaises(ValueError):
            check("p", "")

    def test_independent_engines_do_not_share_state(self):
        other = ReflectEngine()
        other.load("p", "<div>%s</div>" % PAYLOAD)
        # 默认引擎没有这个页面。
        with self.assertRaises(ValueError):
            check("p", "q")
        self.assertEqual(other.check("p", "q")["findings"][0]["verdict"],
                         VERDICT_HIT)

    def test_module_exports_include_new_entry_points(self):
        for name in ("check", "report", "load", "add_entry", "check_all",
                     "reset"):
            self.assertTrue(hasattr(xssreflect, name))


if __name__ == "__main__":
    unittest.main()

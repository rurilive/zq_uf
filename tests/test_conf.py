import json
import os
import tempfile
import unittest

from extractconf import ConfigError, ExtractError, apply, load

PAGE = "标题：测试文章 作者：张三 阅读：128"
HTML_PAGE = ('<html><body><h1 class="title">测试文章</h1>'
             '<div id="meta"><span class="views">128</span></div></body></html>')
JSON_PAGE = '{"article": {"title": "测试文章", "views": "128", "tags": ["科技"]}}'


def write_conf(tmp, conf):
    path = os.path.join(tmp, "conf.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(conf) if not isinstance(conf, str) else conf)
    return path


class ExtractconfTest(unittest.TestCase):
    def test_apply_all_fields(self):
        conf = {"title": r"标题：(\S+)", "author": r"作者：(\S+)"}
        self.assertEqual(apply(PAGE, conf), {"title": "测试文章", "author": "张三"})

    def test_missing_field_is_empty(self):
        self.assertEqual(apply(PAGE, {"price": r"价格：(\d+)"}), {"price": ""})

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "conf.json")
            open(path, "w", encoding="utf-8").write(json.dumps({"title": r"标题：(\S+)"}))
            conf = load(path)
        self.assertEqual(apply(PAGE, conf)["title"], "测试文章")


class RuleTypeTest(unittest.TestCase):
    def test_css_rule(self):
        conf = {"fields": {"title": {"rules": [
            {"name": "css-title", "type": "css", "value": "h1.title"}]}}}
        result = apply(HTML_PAGE, conf)
        self.assertEqual(result["title"], "测试文章")
        self.assertEqual(result.matched["title"], "css-title")

    def test_css_rule_with_child_combinator(self):
        conf = {"fields": {"views": {"rules": [
            {"name": "css-views", "type": "css", "value": "#meta > .views"}]}}}
        self.assertEqual(apply(HTML_PAGE, conf)["views"], "128")

    def test_regex_rule(self):
        conf = {"fields": {"author": {"rules": [
            {"name": "re-author", "type": "regex", "value": r"作者：(\S+)"}]}}}
        self.assertEqual(apply(PAGE, conf)["author"], "张三")

    def test_jsonpath_rule(self):
        conf = {"fields": {"tags": {"rules": [
            {"name": "json-tags", "type": "jsonpath", "value": "$.article.tags[0]"}]}}}
        result = apply(JSON_PAGE, conf)
        self.assertEqual(result["tags"], "科技")
        self.assertEqual(result.matched["tags"], "json-tags")


class FallbackTest(unittest.TestCase):
    def test_first_hit_wins(self):
        conf = {"fields": {"title": {"rules": [
            {"name": "css-title", "type": "css", "value": "h1.title"},
            {"name": "re-title", "type": "regex", "value": r"标题：(\S+)"}]}}}
        result = apply(HTML_PAGE, conf)
        self.assertEqual(result["title"], "测试文章")
        self.assertEqual(result.matched["title"], "css-title")

    def test_fallback_to_second_rule(self):
        conf = {"fields": {"title": {"rules": [
            {"name": "css-missing", "type": "css", "value": "h2.headline"},
            {"name": "re-title", "type": "regex", "value": r"标题：(\S+)"}]}}}
        result = apply(PAGE, conf)
        self.assertEqual(result["title"], "测试文章")
        self.assertEqual(result.matched["title"], "re-title")

    def test_jsonpath_fallback_when_page_not_json(self):
        conf = {"fields": {"title": {"rules": [
            {"name": "json-title", "type": "jsonpath", "value": "$.article.title"},
            {"name": "re-title", "type": "regex", "value": r"标题：(\S+)"}]}}}
        result = apply(PAGE, conf)
        self.assertEqual(result["title"], "测试文章")
        self.assertEqual(result.matched["title"], "re-title")


class DefaultAndTypeTest(unittest.TestCase):
    def test_default_when_no_rule_hits(self):
        conf = {"fields": {"price": {"default": "0", "rules": [
            {"name": "re-price", "type": "regex", "value": r"价格：(\d+)"}]}}}
        result = apply(PAGE, conf)
        self.assertEqual(result["price"], "0")
        self.assertIsNone(result.matched["price"])

    def test_type_conversion(self):
        conf = {"fields": {"views": {"type": "int", "rules": [
            {"name": "re-views", "type": "regex", "value": r"阅读：(\d+)"}]}}}
        self.assertEqual(apply(PAGE, conf)["views"], 128)

    def test_default_on_conversion_failure(self):
        conf = {"fields": {"views": {"type": "int", "default": 0, "rules": [
            {"name": "css-views", "type": "css", "value": "h1.title"}]}}}
        self.assertEqual(apply(HTML_PAGE, conf)["views"], 0)

    def test_required_missing_raises(self):
        conf = {"fields": {"price": {"required": True, "rules": [
            {"name": "re-price", "type": "regex", "value": r"价格：(\d+)"}]}}}
        with self.assertRaisesRegex(ExtractError, "E008.*price"):
            apply(PAGE, conf)


class ConfigErrorTest(unittest.TestCase):
    def assert_config_error(self, conf, code, field=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_conf(tmp, conf)
            with self.assertRaises(ConfigError) as caught:
                load(path)
        message = str(caught.exception)
        self.assertIn(code, message)
        self.assertIn(path, message)
        if field:
            self.assertIn(field, message)

    def test_invalid_json(self):
        self.assert_config_error("{not json", "E001")

    def test_fields_not_object(self):
        self.assert_config_error({"fields": []}, "E002")

    def test_field_not_object(self):
        self.assert_config_error({"fields": {"title": "正则"}}, "E003", "title")

    def test_rules_empty(self):
        self.assert_config_error({"fields": {"title": {"rules": []}}}, "E004", "title")

    def test_unknown_rule_type(self):
        conf = {"fields": {"title": {"rules": [
            {"name": "x", "type": "xpath", "value": "//h1"}]}}}
        self.assert_config_error(conf, "E005", "title")

    def test_bad_regex(self):
        conf = {"fields": {"title": {"rules": [
            {"name": "re", "type": "regex", "value": "标题：("}]}}}
        self.assert_config_error(conf, "E006", "title")

    def test_unknown_converter(self):
        conf = {"fields": {"title": {"type": "date", "rules": [
            {"name": "re", "type": "regex", "value": "x"}]}}}
        self.assert_config_error(conf, "E007", "title")


if __name__ == "__main__":
    unittest.main()


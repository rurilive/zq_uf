import json
import os
import tempfile
import unittest

from extractconf import apply, load

PAGE = "标题：测试文章 作者：张三 阅读：128"


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

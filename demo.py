import json
import os
import tempfile

from extractconf import apply, load

PAGE = "标题：测试文章 作者：张三 阅读：128"
CONF = {"title": r"标题：(\S+)", "author": r"作者：(\S+)", "views": r"阅读：(\d+)"}

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "conf.json")
    open(path, "w", encoding="utf-8").write(json.dumps(CONF))
    conf = load(path)
    print(apply(PAGE, conf))

# 第二段：同一页面跑两份配置，CSS 站点改版后切到正则/JSON 配置即可。
HTML_PAGE = ('<html><body><h1 class="title">测试文章</h1>'
             '<span id="views">128</span></body></html>')

CONF_CSS = {
    "fields": {
        "title": {"rules": [
            {"name": "css-title", "type": "css", "value": "h1.title"},
            {"name": "re-title", "type": "regex", "value": r"标题：(\S+)"}]},
        "views": {"type": "int", "default": 0, "rules": [
            {"name": "css-views", "type": "css", "value": "#views"}]},
        "author": {"default": "佚名", "rules": [
            {"name": "css-author", "type": "css", "value": ".author"}]},
    }
}

CONF_REGEX = {
    "fields": {
        "title": {"rules": [
            {"name": "re-title", "type": "regex", "value": r"<h1[^>]*>([^<]+)</h1>"}]},
        "views": {"type": "int", "default": 0, "rules": [
            {"name": "re-views", "type": "regex", "value": r'id="views">(\d+)'}]},
        "author": {"required": True, "rules": [
            {"name": "re-author", "type": "regex", "value": r"作者：(\S+)"}]},
    }
}

with tempfile.TemporaryDirectory() as tmp:
    for name, conf_dict in [("css", CONF_CSS), ("regex", CONF_REGEX)]:
        path = os.path.join(tmp, name + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(conf_dict, ensure_ascii=False))
        try:
            result = apply(HTML_PAGE, load(path))
            print(name, dict(result), result.matched)
        except Exception as exc:
            print(name, "抽取失败:", exc)


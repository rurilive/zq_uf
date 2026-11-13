from siteprobe import probe

result = probe("http://site/list", lambda url: {
    "status": 200,
    "body": "<html>列表页</html>",
    "headers": {"content-type": "text/html; charset=utf-8"},
})
print(result)

# TODO: 第二段演示（实现后补）

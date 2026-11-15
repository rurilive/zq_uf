from snapdiff import diff, snapshot

a = snapshot({"html": "<p>正文</p>"})
b = snapshot({"html": "<p>正文</p><script>x()</script>"})
print(a)
print(diff(a, b))

# TODO: 第二段演示（实现后补）

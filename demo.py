from pagefinger import fingerprint, similar

print(fingerprint("<html><body><p>正文</p><script>x()</script></body></html>"))
print(similar("<p>正文</p>", "<p>正文</p><script>x()</script>"))

# TODO: 第二段演示（实现后补）

from fingerprint import Fingerprint, digest

store = Fingerprint()
for record in ({"url": "http://s/a"}, {"url": "http://s/a"}, {"url": "http://s/b"}):
    key = digest(record)
    print(key, "已见" if store.seen(key) else "新链接")

# TODO: 第二段演示（实现后补）

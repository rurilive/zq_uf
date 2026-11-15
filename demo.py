from linksieve import due, fingerprint

print(fingerprint("https://shop/p?utm_source=x"))
print(due("https://shop/p", 100))
print(due("https://shop/p", 200))

# TODO: 第二段演示（实现后补）

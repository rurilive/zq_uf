from contenthash import fingerprint, similar

a = "<nav>menu</nav><p>hello</p>"
b = "<nav>menu2</nav><p>hello</p>"
print(fingerprint(a)[:16])
print("similar", similar(a, b))

# TODO: 第二段演示（实现后补）

from confdrift import diff, snapshot

base = snapshot("prod", {"workers": 4, "token": "abc"})
target = snapshot("prod", {"workers": 8, "token": "abc"})
print(diff(base, target))

# TODO: 第二段演示（实现后补）
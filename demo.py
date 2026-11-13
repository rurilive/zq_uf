from siteweight import feedback, next_site

for _ in range(4):
    site = next_site()
    print(site)
    feedback(site, True)

# TODO: 第二段演示（实现后补）

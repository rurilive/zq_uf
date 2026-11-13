from linkguard import follow

print(follow("http://s/a", lambda url: {"status": 200, "from": url}))


def show(title, report):
    print("\n== %s ==" % title)
    print("final_url:", report["final_url"], "| redirects:", report["redirects"],
          "| cycle:", report["cycle"], "| error:", report["error"])
    print("chain:", " -> ".join(report["chain"]))
    for hop in report["cross_domain"]:
        print("cross-domain:", hop["from"], "->", hop["to"])


# 1) 重定向环：/a -> /b -> /a
loop = {
    "http://s/a": {"status": 302, "location": "/b"},
    "http://s/b": {"status": 302, "location": "/a"},
}
show("重定向环", follow("http://s/a", lambda url: loop[url]))


# 2) 会话参数：同一页带不同 sid，归并后判环
def fetch_sid(url):
    sid = "s%d" % (abs(hash(url)) % 1000)
    return {"status": 302, "location": "/home?sid=" + sid}


show("会话参数归并", follow("http://s/home", fetch_sid))


# 3) 跨域跳转：记录来源和目标，默认不跟随
cross = {"http://s/page": {"status": 302, "location": "http://mirror.example.com/page"}}
show("跨域跳转", follow("http://s/page", lambda url: cross[url]))

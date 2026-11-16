from sqldetect import check, load, report

URL = "http://app.local/item"
RESPONSES = {
    "id": "You have an error in your SQL syntax near id",
    "name": "welcome back",
    "page": "unknown column error",
}

print("登记参数", load(URL, RESPONSES)["params"])
out = check(URL, ["id", "name"])
print("可疑参数", [item["param"] for item in out["findings"]])
print("请求样例", out["findings"][0]["request"])
print("重复项", report()["duplicates"], "结论数", report()["findings"])

# TODO: 第二段演示（实现后补）

"""演示：半成功批次 -> 幂等重提 -> resume 失败项 -> 对账导出。"""

from formbatch import configure, export_detail, resume, submit

configure(global_rate=100, site_rate=50, max_retries=2, backoff_base=0.01)

_state = {"failed_once": set()}


def half_success_sender(form, seq):
    """模拟服务端：偶数序号首次 500（重试后成功）；bad 永远 400。"""
    name = form["name"]
    if name == "bad":
        return {"ok": False, "category": "client", "message": "HTTP 400 参数错误"}
    if seq % 2 == 0 and name not in _state["failed_once"]:
        _state["failed_once"].add(name)
        return {"ok": False, "category": "server", "message": "HTTP 500 服务端错误"}
    return {"ok": True, "seq": seq}


def show(title, report):
    s = report["summary"]
    print("%s: 成功 %d 失败 %d 跳过 %d 限速等待 %d 次/%.3fs"
          % (title, s["succeeded"], s["failed"], s["skipped"],
             s["rate_limit_waits"], s["rate_limit_wait_seconds"]))


forms = [{"name": "user%d" % i, "site": "api.example.com"} for i in range(6)]
forms.append({"name": "bad", "site": "api.example.com"})

first = submit(forms, sender=half_success_sender)
show("首次提交（半成功）", first)

again = submit(forms, sender=half_success_sender)
show("原样重提（成功项幂等跳过，失败项单独重试）", again)

resumed = resume(first["batch"], sender=half_success_sender)
show("resume 首批次失败项", resumed)

path = "/tmp/formbatch-detail.jsonl"
rows = export_detail(first["batch"], path)
print("对账明细 %d 行，已导出 %s" % (len(rows), path))
for row in rows:
    print("  seq=%d status=%s category=%s" % (row["seq"], row["status"],
                                              row["category"] or "-"))

"""分叉流程演示：fetch 后分两支解析，merge 汇合再入库，并演示失败恢复。"""

import os

import crawlflow
from crawlflow import resume, run

ROOT = os.path.dirname(os.path.abspath(__file__))
crawlflow.set_base_dir(os.path.join(ROOT, "demo_out"))

_state = {"meta_ok": False}  # 首次运行让 parse_meta 失败，演示局部重跑


def parse_html(context):
    return {"items_html": ["html:" + context["html"]]}


def parse_meta(context):
    if not _state["meta_ok"]:
        raise RuntimeError("meta source unavailable")
    return {"items_meta": ["meta:" + context["html"]]}


def merge(context):
    return {"items": context["items_html"] + context["items_meta"]}


crawlflow.register_handler("parse_html", parse_html)
crawlflow.register_handler("parse_meta", parse_meta)
crawlflow.register_handler("merge", merge)

flow = crawlflow.load_flow(os.path.join(ROOT, "flows", "demo.json"))

first = run(flow, {"seed": "https://shop/"})
print("首次执行:", first["status"])
for row in first["stages"]:
    print("  {stage:<10} {status}".format(**row))

_state["meta_ok"] = True
second = resume(first["token"])
print("恢复后:", second["status"], "| 重跑:", second["rerun"])
print("统计:", {k: v for k, v in second["stats"].items()})
print("产物:", crawlflow.list_artifacts(second["run_id"]))
print("入库数:", crawlflow.get_artifact(second["run_id"], "store", "stored"))

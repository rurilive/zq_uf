"""审计导出，当前全量读。"""
import json


def export(range_spec, path):
    """一次读完写文件。"""
    records = range_spec.get("records") or []
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"rows": len(records)}


def resume(token):
    raise NotImplementedError("resume not supported")

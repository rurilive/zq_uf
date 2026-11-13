"""可同步的元数据登记中心：字段推断、差异分类、别名归一、版本历史。"""
from datetime import datetime, timezone

from .infer import infer_fields, infer_from_csv


def _normalize_field(field):
    if isinstance(field, str):
        return {"name": field, "type": None, "null_rate": None}
    return {
        "name": field["name"],
        "type": field.get("type"),
        "null_rate": field.get("null_rate"),
    }


class Registry:
    def __init__(self, aliases=None, downstream=None):
        self.aliases = dict(aliases or {})
        self.downstream = {name: list(tasks) for name, tasks in (downstream or {}).items()}
        self.datasets = {}

    def _canonical(self, name):
        return self.aliases.get(name, name)

    def _source_fields(self, source):
        if "fields" in source:
            return [_normalize_field(field) for field in source["fields"]]
        if "rows" in source:
            return infer_fields(source["rows"])
        if "path" in source:
            return infer_from_csv(source["path"])
        return []

    def _normalize_fields(self, fields):
        pairs = [(self._canonical(field["name"]), field) for field in fields]
        merged = {}
        for canonical, field in pairs:
            if field["name"] == canonical:
                merged[canonical] = {**field, "name": canonical}
        for canonical, field in pairs:
            merged.setdefault(canonical, {**field, "name": canonical})
        return merged

    def register(self, dataset):
        name = self._canonical(dataset["name"])
        fields = self._normalize_fields(self._source_fields(dataset))
        history = self.datasets.setdefault(name, [])
        history.append({
            "version": len(history) + 1,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "fields": fields,
        })
        return list(fields)

    def _latest(self, name):
        history = self.datasets.get(name, [])
        return history[-1]["fields"] if history else {}

    def diff(self, source):
        name = self._canonical(source.get("name"))
        registered = self._latest(name)
        incoming = self._normalize_fields(self._source_fields(source))
        added = [field for field in incoming if field not in registered]
        removed = [field for field in registered if field not in incoming]
        type_changed = []
        for field in incoming.keys() & registered.keys():
            old = registered[field].get("type")
            new = incoming[field].get("type")
            if old and new and old != new:
                type_changed.append({"field": field, "from": old, "to": new})
        changed = bool(added or removed or type_changed)
        return {
            "added": added,
            "removed": removed,
            "type_changed": type_changed,
            "affected_tasks": list(self.downstream.get(name, [])) if changed else [],
        }

    def version(self, name):
        return len(self.datasets.get(self._canonical(name), []))

    def get(self, name, version=None):
        history = self.datasets.get(self._canonical(name), [])
        if not history:
            return None
        if version is None:
            return history[-1]
        for record in history:
            if record["version"] == version:
                return record
        return None

    def history(self, name):
        return list(self.datasets.get(self._canonical(name), []))

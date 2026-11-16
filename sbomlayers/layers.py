"""镜像组件清单：按层记录增删，合并后以最终文件系统为准。

一次扫描（:func:`sbom`）返回合并后的组件清单、每个组件所在层、镜像摘要、
扫描时间，以及相对同镜像上一次扫描的差异。:func:`report` 用于按镜像查看，
或直接对比两个镜像的组件变化。

合并口径
--------
层按传入顺序从基础层到最上层处理：

* ``add`` 新增组件；同名组件在更高层再次出现时以更高层为准（版本/许可证覆盖，
  更高层没给版本或许可证时沿用低层信息），清单里仍然只保留一条，
  ``layers`` 记录它出现过的所有层。
* ``remove`` 删除组件；删除后不把组件从清单里抹掉，而是保留一条
  ``status="removed"`` 的记录，用 ``removed_in`` 标出删除发生的层。
* 最终组件状态即最终文件系统状态；同一组件只出现一次。

镜像摘要与扫描时间
------------------
``image`` 可显式传 ``digest``/``scanned_at``；未传时摘要由镜像名与各层内容
确定性计算（``sha256:...``），时间取当前 UTC 时间。同一内容重复扫描摘要稳定。
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

__all__ = ["sbom", "report", "reset"]

_IMAGES: dict[str, dict] = {}
_HISTORY: dict[str, list[dict]] = {}

_ADD_KEYS = ("add", "added", "adds")
_REMOVE_KEYS = ("remove", "removed", "removes")
_DIGEST_KEYS = ("digest", "image_digest", "source_digest", "scan_digest")
# 组件上标注来源镜像摘要的字段。``digest`` 放最后兼容把镜像摘要写在组件上的清单。
_PROVENANCE_KEYS = ("image_digest", "source_digest", "scan_digest", "digest")
_TIME_KEYS = ("scanned_at", "scannedAt", "timestamp", "time")


# ---------------------------------------------------------------------------
# 规范化辅助
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_license(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _norm_license(value.get("id") or value.get("name") or value.get("license"))
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            text = _norm_license(item)
            if text:
                parts.append(text)
        return ", ".join(parts)
    return str(value)


def _norm_component(item) -> dict:
    """把 add 项规范化成 ``{name, version, license, digest}``。"""
    if isinstance(item, str):
        name, version, license_, digest = item.strip(), "", "", ""
    elif isinstance(item, dict):
        name = str(item.get("name") or item.get("package") or "").strip()
        version = str(item.get("version") or "").strip()
        license_ = ""
        for key in ("license", "licence", "licenses"):
            license_ = _norm_license(item.get(key))
            if license_:
                break
        digest = ""
        for key in _PROVENANCE_KEYS:
            if item.get(key):
                digest = str(item[key])
                break
    else:
        raise ValueError("component must be a name or an object")
    if not name:
        raise ValueError("component name is required")
    return {"name": name, "version": version, "license": license_, "digest": digest}


def _norm_remove(item) -> str:
    if isinstance(item, str):
        name = item.strip()
    elif isinstance(item, dict):
        name = str(item.get("name") or item.get("package") or "").strip()
    else:
        raise ValueError("removed component must be a name or an object")
    if not name:
        raise ValueError("removed component name is required")
    return name


def _pick(mapping: dict, keys, default=None):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _dedupe_warnings(warnings: list[dict]) -> list[dict]:
    """同一组件在多条路径上触发同一条提示时只保留一条。"""
    seen, deduped = set(), []
    for warning in warnings:
        key = json.dumps(warning, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(warning)
    return deduped


def _normalize_layers(layers) -> list[dict]:
    """把原始层列表规范化为 ``[{id, digest, adds, removes}]``。"""
    if layers is None:
        return []
    if not isinstance(layers, (list, tuple)):
        raise ValueError("layers must be a list")
    normalized = []
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ValueError(f"layer #{index} must be an object")
        raw_id = layer.get("id")
        layer_id = str(raw_id) if raw_id not in (None, "") else f"layer{index + 1}"

        adds = _pick(layer, _ADD_KEYS, []) or []
        removes = _pick(layer, _REMOVE_KEYS, []) or []
        if isinstance(adds, (str, dict)):
            adds = [adds]
        if isinstance(removes, (str, dict)):
            removes = [removes]
        if not isinstance(adds, (list, tuple)):
            raise ValueError(f"layer {layer_id!r}: add must be a list")
        if not isinstance(removes, (list, tuple)):
            raise ValueError(f"layer {layer_id!r}: remove must be a list")

        # 同一层内同名组件只保留一条，后出现的覆盖前面的版本/许可证。
        deduped: dict[str, dict] = {}
        for item in adds:
            component = _norm_component(item)
            deduped[component["name"]] = component

        normalized.append({
            "id": layer_id,
            "digest": str(layer.get("digest") or ""),
            "adds": list(deduped.values()),
            "removes": list(dict.fromkeys(_norm_remove(item) for item in removes)),
        })
    return normalized


# ---------------------------------------------------------------------------
# 摘要 / 合并
# ---------------------------------------------------------------------------
def _compute_digest(image_name: str, layers: list[dict]) -> str:
    payload = {
        "image": image_name,
        "layers": [
            {
                "id": layer["id"],
                "add": layer["adds"],
                "remove": layer["removes"],
            }
            for layer in layers
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _merge_layers(layers: list[dict], image_digest: str):
    """逐层合并，返回 ``(组件状态, 逐层记录, 摘要对不上的提示)``。"""
    state: dict[str, dict] = {}
    layer_records = []
    warnings = []

    for layer in layers:
        layer_id = layer["id"]
        added, removed, replaced = [], [], []

        for component in layer["adds"]:
            name = component["name"]
            entry = state.get(name)
            if entry is None:
                entry = {
                    "name": name,
                    "version": component["version"],
                    "license": component["license"],
                    "layer": layer_id,
                    "layers": [layer_id],
                    "status": "present",
                    "removed_in": None,
                }
                state[name] = entry
            else:
                previous_version = entry["version"]
                if entry["status"] == "removed":
                    entry["status"] = "present"
                    entry["removed_in"] = None
                # 更高层没给出可用版本时沿用低层信息，不把已知版本抹成空。
                if component["version"] and component["version"] != previous_version:
                    replaced.append({
                        "name": name,
                        "from": previous_version,
                        "to": component["version"],
                    })
                    entry["version"] = component["version"]
                if component["license"]:
                    entry["license"] = component["license"]
                entry["layer"] = layer_id
                if layer_id not in entry["layers"]:
                    entry["layers"].append(layer_id)

            if component["digest"] and image_digest and component["digest"] != image_digest:
                warnings.append({
                    "type": "component_digest_mismatch",
                    "image": None,
                    "name": name,
                    "version": component["version"],
                    "component_digest": component["digest"],
                    "image_digest": image_digest,
                    "message": "组件记录的镜像摘要与本次扫描摘要不一致",
                })
            added.append({
                "name": name,
                "version": component["version"],
                "license": component["license"],
            })

        for name in layer["removes"]:
            entry = state.get(name)
            if entry is None:
                # 删除记录也要保留：基础镜像里未必逐条列出过该组件。
                entry = {
                    "name": name,
                    "version": "",
                    "license": "",
                    "layer": "",
                    "layers": [],
                    "status": "removed",
                    "removed_in": layer_id,
                }
                state[name] = entry
            elif entry["status"] == "present":
                entry["status"] = "removed"
                entry["removed_in"] = layer_id
            removed.append({"name": name, "version": entry["version"]})

        layer_records.append({
            "id": layer_id,
            "digest": layer["digest"],
            "added": added,
            "removed": removed,
            "replaced": replaced,
        })

    components = [copy.deepcopy(state[name]) for name in sorted(state)]
    for component in components:
        component["removed"] = component["status"] == "removed"
    return components, layer_records, warnings


# ---------------------------------------------------------------------------
# 差异
# ---------------------------------------------------------------------------
def _by_name(components) -> dict[str, dict]:
    return {component["name"]: component for component in (components or [])}


def _diff_components(previous, current) -> list[dict]:
    """按组件名对齐，分别产出新增、删除、版本变化。"""
    before, after = _by_name(previous), _by_name(current)
    diffs = []
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        old_present = bool(old) and old.get("status") == "present"
        new_present = bool(new) and new.get("status") == "present"

        if old_present and not new_present:
            diffs.append({
                "name": name,
                "change": "removed",
                "from": old.get("version", ""),
                "to": None,
                "from_layer": old.get("layer", ""),
                "to_layer": (new or {}).get("removed_in") or "",
            })
        elif new_present and not old_present:
            diffs.append({
                "name": name,
                "change": "added",
                "from": None,
                "to": new.get("version", ""),
                "from_layer": "",
                "to_layer": new.get("layer", ""),
            })
        elif old_present and new_present and old.get("version", "") != new.get("version", ""):
            diffs.append({
                "name": name,
                "change": "version_changed",
                "from": old.get("version", ""),
                "to": new.get("version", ""),
                "from_layer": old.get("layer", ""),
                "to_layer": new.get("layer", ""),
            })
    return diffs


def _component_snapshot(components) -> dict[str, str]:
    """用于判断同一摘要下组件是否一致：仅看最终存在的组件。"""
    return {
        component["name"]: component.get("version", "")
        for component in (components or [])
        if component.get("status") == "present"
    }


def _compare_records(first: dict, second: dict) -> dict:
    """对比两个镜像：按组件名对齐，新增/删除/版本变化分开统计。"""
    added, removed, version_changed = [], [], []
    left = _by_name(first["components"])
    right = _by_name(second["components"])

    for name in sorted(set(left) | set(right)):
        old, new = left.get(name), right.get(name)
        old_present = bool(old) and old.get("status") == "present"
        new_present = bool(new) and new.get("status") == "present"

        if new_present and not old_present:
            added.append({
                "name": name,
                "version": new.get("version", ""),
                "license": new.get("license", ""),
                "layer": new.get("layer", ""),
            })
        elif old_present and not new_present:
            removed.append({
                "name": name,
                "version": old.get("version", ""),
                "license": old.get("license", ""),
                "layer": old.get("layer", ""),
            })
        elif old_present and new_present:
            if old.get("version", "") != new.get("version", ""):
                version_changed.append({
                    "name": name,
                    "from": old.get("version", ""),
                    "to": new.get("version", ""),
                    "from_license": old.get("license", ""),
                    "to_license": new.get("license", ""),
                })

    unchanged = sum(
        1
        for name in set(left) & set(right)
        if left[name].get("status") == right[name].get("status") == "present"
        and left[name].get("version", "") == right[name].get("version", "")
    )

    warnings = []
    if first["digest"] and first["digest"] == second["digest"]:
        if _component_snapshot(first["components"]) != _component_snapshot(second["components"]):
            warnings.append({
                "type": "digest_components_mismatch",
                "images": [first["image"], second["image"]],
                "digest": first["digest"],
                "message": "两个镜像摘要相同，但组件版本不一致",
            })

    return {
        "images": [first["image"], second["image"]],
        "digests": {first["image"]: first["digest"], second["image"]: second["digest"]},
        "added": added,
        "removed": removed,
        "version_changed": version_changed,
        "changed": version_changed,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "version_changed": len(version_changed),
            "unchanged": unchanged,
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def reset():
    """清空镜像记录与扫描历史。"""
    _IMAGES.clear()
    _HISTORY.clear()
    return {"images": 0}


def sbom(image):
    """扫描一个镜像并返回合并后的组件清单。

    ``image`` 形如::

        {"image": "app:1",
         "digest": "sha256:...",          # 可选，不传则按层内容计算
         "scanned_at": "2026-01-01T00:00:00+00:00",   # 可选，默认当前时间
         "layers": [{"id": "l1",
                     "add": [{"name": "openssl", "version": "1.1",
                              "license": "OpenSSL"}],
                     "remove": ["curl"]}]}

    返回的 ``components`` 每个组件只出现一次（按名称排序），字段包括
    ``name``/``version``/``license``/``layer``/``layers``/``status``/
    ``removed_in``；被删除的组件保留在清单中并标记 ``status="removed"``。
    ``diff`` 为本镜像相对上一次扫描的变化。
    """
    if not isinstance(image, dict) or not image.get("image"):
        raise ValueError("image is required")
    image_name = str(image["image"])

    layers = _normalize_layers(image.get("layers"))
    explicit_digest = _pick(image, _DIGEST_KEYS, "")
    digest = str(explicit_digest) if explicit_digest else _compute_digest(image_name, layers)
    scanned_at = _pick(image, _TIME_KEYS, "")
    scanned_at = str(scanned_at) if scanned_at else _now_iso()

    components, layer_records, warnings = _merge_layers(layers, digest)
    for warning in warnings:
        warning["image"] = image_name
    warnings = _dedupe_warnings(warnings)

    previous_record = _IMAGES.get(image_name)
    previous_components = previous_record["components"] if previous_record else []
    # 首次扫描没有可比对的基准，差异为空；组件清单本身已说明全部内容。
    diff = _diff_components(previous_components, components) if previous_record else []

    # 同一镜像摘要在两次扫描间组件却不同，说明组件版本与摘要对不上。
    if (
        previous_record
        and previous_record.get("digest")
        and previous_record["digest"] == digest
        and _component_snapshot(previous_components) != _component_snapshot(components)
    ):
        warnings.append({
            "type": "digest_components_mismatch",
            "image": image_name,
            "digest": digest,
            "message": "镜像摘要未变但组件版本发生变化",
        })

    warnings = _dedupe_warnings(warnings)

    present = sum(1 for component in components if component["status"] == "present")
    removed = len(components) - present
    record = {
        "image": image_name,
        "digest": digest,
        "scanned_at": scanned_at,
        "components": components,
        "layers": layer_records,
        "layer_count": len(layer_records),
        "diff": diff,
        "warnings": warnings,
        "counts": {
            "components": present,
            "removed": removed,
            "layers": len(layer_records),
        },
    }

    _IMAGES[image_name] = record
    _HISTORY.setdefault(image_name, []).append(copy.deepcopy(record))
    return copy.deepcopy(record)


def _aggregate_report() -> dict:
    records = [_IMAGES[name] for name in sorted(_IMAGES)]
    present = sum(record["counts"]["components"] for record in records)
    removed = sum(record["counts"]["removed"] for record in records)

    diff = []
    for record in records:
        for item in record["diff"]:
            diff.append(dict(item, image=record["image"]))

    warnings = []
    for record in records:
        warnings.extend(copy.deepcopy(record["warnings"]))

    digest_parts = sorted(f"{record['image']}={record['digest']}" for record in records)
    combined = ""
    if digest_parts:
        combined = "sha256:" + hashlib.sha256(
            "\n".join(digest_parts).encode("utf-8")
        ).hexdigest()

    return {
        "images": len(records),
        "components": present,
        "removed": removed,
        "digest": combined,
        "scanned_at": max((record["scanned_at"] for record in records), default=""),
        "diff": diff,
        "warnings": warnings,
        "details": {
            record["image"]: {
                "digest": record["digest"],
                "scanned_at": record["scanned_at"],
                "components": record["counts"]["components"],
                "removed": record["counts"]["removed"],
                "layers": record["counts"]["layers"],
                "scans": len(_HISTORY.get(record["image"], [])),
            }
            for record in records
        },
    }


def report(image=None, other=None):
    """按镜像查看组件清单，或对比两个镜像的组件变化。

    * ``report()``：所有镜像的汇总（``components`` 为最终存在的组件总数，
      同镜像内已去重；``removed`` 为保留的删除记录数）。
    * ``report("app:1")``：单个镜像的完整清单、所在层、摘要、时间和最近差异。
    * ``report("app:1", "app:2")``：两个镜像按组件名对齐，``added``/
      ``removed``/``version_changed`` 分开统计。
    """
    if other is not None:
        return _compare_records(_require(image), _require(other))
    if isinstance(image, (list, tuple)):
        if len(image) == 2:
            return _compare_records(_require(image[0]), _require(image[1]))
        raise ValueError("report(image) expects a name or a pair of names")
    if image is not None:
        return copy.deepcopy(_require(image))
    return _aggregate_report()


def _require(image_name) -> dict:
    if image_name not in _IMAGES:
        raise KeyError(f"image not scanned: {image_name}")
    return _IMAGES[image_name]

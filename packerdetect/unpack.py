"""脱壳入口与按家族管理的脱壳参数。

设计要点：

- 先 detect 再脱壳，脱壳参数按家族保存，同家族样本自动复用；
- 已知家族有默认参数，可通过 set_unpack_params 覆盖/补充，支持落盘复用；
- unknown/unpacked 不硬脱：unknown 必须显式给参数才尝试，unpacked 直接跳过；
- 脱壳失败时保留原样本路径和失败原因，不产出伪造结果；
- 成功后对前后样本跑 diff，重新识别并逐项列出结构/导入表差异。
"""

import copy
import json
import os

from .scan import detect, diff
from .signatures import DEFAULT_UNPACK_PARAMS, UNPACK_BLOCKERS


class UnpackParamStore:
    """按家族保存脱壳参数；默认内存态，可选 JSON 文件持久化。"""

    def __init__(self, path=None):
        self._path = path
        self._params = copy.deepcopy(DEFAULT_UNPACK_PARAMS)
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if not isinstance(stored, dict):
                raise ValueError("参数文件格式错误：根节点必须是对象")
            for family, params in stored.items():
                self._params.setdefault(family, {}).update(params)

    def get(self, family):
        return copy.deepcopy(self._params.get(family))

    def set(self, family, params):
        if not isinstance(params, dict):
            raise ValueError("脱壳参数必须是字典")
        self._params.setdefault(family, {}).update(params)
        if self._path:
            self.save(self._path)
        return self.get(family)

    def families(self):
        return sorted(self._params)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self._params, handle, ensure_ascii=False, indent=2, sort_keys=True)

    def load(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if not isinstance(stored, dict):
            raise ValueError("参数文件格式错误：根节点必须是对象")
        for family, params in stored.items():
            self._params.setdefault(family, {}).update(params)
        self._path = path


# 模块级默认参数库，detect/unpack 直接可用
_STORE = UnpackParamStore()


def get_unpack_params(family):
    """取某家族当前脱壳参数（副本，改不影响库内数据）。"""
    return _STORE.get(family)


def set_unpack_params(family, params, path=None):
    """按家族更新脱壳参数；给 path 时同时落盘，便于跨样本/跨运行复用。"""
    if path is not None:
        global _STORE
        _STORE = UnpackParamStore(path)
    return _STORE.set(family, params)


def _blocked_behavior(sample, params):
    """样本行为里存在阻断项、而参数 bypass 没覆盖时返回该项。"""
    bypass = {str(item).lower() for item in params.get("bypass", [])}
    for behavior in (str(item).lower() for item in sample.get("behavior", [])):
        if behavior in UNPACK_BLOCKERS and behavior not in bypass:
            return behavior
    return None


def _rebuild(sample, detection, params):
    """模拟脱壳后的样本重建。

    样本可携带 payload（真实原始程序的线索）：给了就按 payload 还原，
    没给就按通用规则重建——去掉壳区段、入口回到 .text、导入表用 hidden_imports 补齐。
    """
    result = copy.deepcopy(sample)
    payload = sample.get("payload")
    if isinstance(payload, dict):
        result["sections"] = payload.get("sections", [".text", ".rdata", ".data"])
        result["entry"] = payload.get("entry", "0x401000")
        result["imports"] = payload.get("imports", [])
        result["behavior"] = payload.get("behavior", [])
    else:
        kept = [
            section for section in sample.get("sections", [])
            if str(section if isinstance(section, str) else section.get("name", "")).lower()
            in (".text", ".rdata", ".data", ".rsrc", ".idata")
        ]
        result["sections"] = kept or [".text", ".rdata", ".data"]
        result["entry"] = params.get("oep", "0x401000")
        hidden = sample.get("hidden_imports", [])
        known = [name for name in sample.get("imports", [])]
        result["imports"] = known + [name for name in hidden if name not in known]
        result["behavior"] = [
            name for name in sample.get("behavior", [])
            if name not in ("alloc_rwx", "self_modify", "dynamic_api_resolve",
                            "anti_debug", "anti_vm", "process_hollow")
        ]
    result.pop("payload", None)
    result["name"] = sample.get("name", "") + ".unpacked"
    return result


def unpack(sample, params=None):
    """对样本脱壳，返回检测结论、脱壳结果和前后差异。

    成功::

        {ok: True, packer, confidence, evidence, params, unpacked, diff, ...}

    失败（含不具备脱壳条件）::

        {ok: False, packer, reason, reason_detail, original, ...}

    其中 original 保留原样本，reason 为机器可读原因码。
    """
    if not isinstance(sample, dict):
        raise ValueError("sample 必须是字典")

    detection = detect(sample)
    packer = detection["packer"]
    base = {
        "sample": sample.get("name", ""),
        "packer": packer,
        "confidence": detection["confidence"],
        "evidence": detection["evidence"],
        "pending": detection["pending"],
    }

    if packer == "unpacked":
        return dict(base, ok=False, reason="not_packed",
                    reason_detail="样本未检测到加壳特征，无需脱壳", original=copy.deepcopy(sample))

    family_params = _STORE.get(packer) if packer != "unknown" else None
    if params:
        family_params = dict(family_params or {}, **params)
    if not family_params:
        return dict(base, ok=False, reason="no_unpack_params",
                    reason_detail="未知壳家族且未提供脱壳参数，保留人工处理",
                    original=copy.deepcopy(sample))

    blocker = _blocked_behavior(sample, family_params)
    if blocker:
        code, detail = UNPACK_BLOCKERS[blocker]
        return dict(base, ok=False, reason=code, reason_detail=detail,
                    params=family_params, original=copy.deepcopy(sample))

    unpacked = _rebuild(sample, detection, family_params)
    comparison = diff(sample, unpacked)
    return dict(base, ok=True, reason=None, reason_detail=None,
                params=family_params, unpacked=unpacked, diff=comparison,
                clean=comparison["clean"])

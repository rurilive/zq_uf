"""模式归一化与单字段校验。

字段处理策略（按字段，不按整行）：
- 必填缺失/不可修复 -> 隔离
- 可空缺失 -> 按类型补默认值（数字补 0，绝不补空串）
- 枚举越界 -> 降级到 degrade 值并记录
- 类型可收敛（如 "12" -> 12）-> 收敛并保留原值来源
"""

# 缺失哨兵：区别于调用方显式传入的 None（None 同样视为缺失）。
MISSING = object()

# 各类型的类型安全默认值，避免“数字字段补空串入库报类型错误”。
TYPE_DEFAULTS = {
    "string": "",
    "number": 0,
    "boolean": False,
    "enum": None,
}

# 隔离类原因 / 修补类原因
ISOLATE = "isolated"
PATCH = "patched"
OK = "ok"


def normalize_schema(schema):
    """把外部 schema 归一化为统一的 fields 映射。

    兼容两种写法：
    - 顶层 ``required`` 名单（未声明的必填名按 string 处理）
    - ``fields`` 中逐项声明 type/default/required/degrade
    """
    schema = schema or {}
    required_names = list(schema.get("required", []))
    raw_fields = schema.get("fields", {}) or {}

    fields = {}
    for name, spec in raw_fields.items():
        spec = dict(spec or {})
        spec.setdefault("type", "string")
        spec["required"] = bool(spec.get("required", name in required_names))
        if "default" not in spec:
            spec["default"] = TYPE_DEFAULTS.get(spec["type"])
        if spec["type"] == "enum":
            spec.setdefault("degrade", spec["default"])
            spec.setdefault("values", [])
        fields[name] = spec

    for name in required_names:
        # 裸 required 名单无类型信息：不做类型收敛，原值原样透传，仅判断缺失
        fields.setdefault(
            name,
            {"type": "any", "required": True, "default": None, "degrade": None},
        )
    return {"fields": fields}


def _isolate(reason, original):
    return ISOLATE, None, {
        "reason": reason,
        "action": "isolate",
        "source": "none",
        "original": original,
    }


def _default(spec, reason, original):
    return PATCH, spec["default"], {
        "reason": reason,
        "action": "default",
        "source": "default",
        "original": original,
    }


def inspect_field(spec, value):
    """校验单个字段。

    返回 ``(outcome, final_value, detail)``：
    - outcome: ``ok`` / ``patched`` / ``isolated``
    - final_value: 应写回行内的值（隔离时为 None）
    - detail: reason/action/source/original，用于修补留痕
    """
    # 1) 缺失：键不存在或显式 None
    if value is MISSING or value is None:
        if spec["required"]:
            return _isolate("missing_required", None)
        return _default(spec, "missing", None)

    ftype = spec["type"]

    # 2) 枚举：越界一律降级并记录（即使必填也降级，不隔离）
    if ftype == "enum":
        if value in spec.get("values", []):
            return OK, value, {"reason": None, "action": None,
                               "source": "original", "original": value}
        return PATCH, spec["degrade"], {
            "reason": "enum_out_of_range",
            "action": "degrade",
            "source": "degrade",
            "original": value,
        }

    # 3) 数字：bool 不算数字；字符串尝试收敛，失败则按非法类型处理
    if ftype == "number":
        if isinstance(value, bool):
            pass  # 落到非法类型分支
        elif isinstance(value, (int, float)):
            return OK, value, {"reason": None, "action": None,
                               "source": "original", "original": value}
        elif isinstance(value, str):
            text = value.strip()
            try:
                number = float(text) if "." in text else int(text)
            except ValueError:
                pass
            else:
                return PATCH, number, {
                    "reason": "coerced_type",
                    "action": "coerce",
                    "source": "original",
                    "original": value,
                }
        # 非法类型（含空串）：必填隔离，可空补类型安全默认值
        if spec["required"]:
            return _isolate("invalid_type", value)
        return _default(spec, "invalid_type", value)

    # 4) 布尔：仅接受 bool
    if ftype == "boolean":
        if isinstance(value, bool):
            return OK, value, {"reason": None, "action": None,
                               "source": "original", "original": value}
        if spec["required"]:
            return _isolate("invalid_type", value)
        return _default(spec, "invalid_type", value)

    # 5) 字符串：非字符串收敛为 str，保留原值
    if ftype == "string":
        if isinstance(value, str):
            return OK, value, {"reason": None, "action": None,
                               "source": "original", "original": value}
        return PATCH, str(value), {
            "reason": "coerced_type",
            "action": "coerce",
            "source": "original",
            "original": value,
        }

    # 未知类型原样放行
    return OK, value, {"reason": None, "action": None,
                       "source": "original", "original": value}

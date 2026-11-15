"""契约规范化：字符串简写与完整 dict 形式统一成内部结构。"""

# 区分"没有 default 键"和 default=None
MISSING = object()

# 数值类型宽度排序：读取方向收紧（rank 变小）破坏，写入方向放宽（rank 变大）破坏
NUMERIC_RANK = {"short": 1, "int": 2, "long": 3, "float": 4, "double": 5}


def type_family(ftype):
    """同一家族内只论宽度（short<int<long<float<double），跨家族互不兼容。"""
    return "numeric" if ftype in NUMERIC_RANK else ftype


def normalize_field(field):
    if isinstance(field, str):
        field = {"type": field}
    elif not isinstance(field, dict):
        # 已是规范化结构（MISSING 哨兵不是 dict，必须先拦住，
        # 否则 isinstance(MISSING, object) 恒真会把它当成字段 dict）
        return field
    normalized = {
        "type": field["type"],
        "required": bool(field.get("required", False)),
        # 不能用 field.get("default", MISSING)：二次规范化时值本身就是 MISSING，
        # dict.get 会把这个哨兵当成有效值返回而不是回退默认参数
        "default": field["default"] if "default" in field else MISSING,
        "enum": list(field["enum"]) if field.get("enum") is not None else None,
        "fields": None,
    }
    if normalized["type"] == "record":
        normalized["fields"] = {
            name: normalize_field(sub)
            for name, sub in field.get("fields", {}).items()
        }
    return normalized


def normalize_contract(contract):
    """返回深拷贝的规范化契约，调用方后续修改不影响入参。"""
    return {
        "fields": {
            name: normalize_field(f)
            for name, f in contract.get("fields", {}).items()
        }
    }


def render_value(value):
    """输出用：MISSING 渲染成 None，保证结果可 JSON 序列化。"""
    return None if value is MISSING else value

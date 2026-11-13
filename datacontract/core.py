"""数据契约注册中心：声明、样本校验、兼容分级、版本历史与回滚。"""


class BreakingChangeError(Exception):
    """注册时检测到破坏性变更且未显式放行。"""

    def __init__(self, report):
        self.report = report
        fields = ", ".join(item["field"] for item in report["breaking"])
        super().__init__("breaking changes blocked: %s" % fields)


# 类型放宽方向（反向即为收紧，判破坏性）。
_WIDENING = {("int", "float"), ("int", "str"), ("float", "str"), ("bool", "str")}

_TYPE_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "str": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
}


class Field:
    def __init__(self, name, type="str", nullable=True, enum=None, default=None):
        self.name = name
        self.type = type
        self.nullable = nullable
        self.enum = list(enum) if enum is not None else None
        self.default = default

    @classmethod
    def from_spec(cls, spec):
        if isinstance(spec, str):
            return cls(spec)
        if isinstance(spec, Field):
            return spec
        return cls(
            spec["name"],
            type=spec.get("type", "str"),
            nullable=spec.get("nullable", True),
            enum=spec.get("enum"),
            default=spec.get("default"),
        )


class Contract:
    """一份带版本的契约：字段声明 + 消费方订阅。"""

    def __init__(self, name, version, fields, consumers=None):
        self.name = name
        self.version = version
        self.fields = [Field.from_spec(f) for f in fields]
        # consumers: {消费方名: [字段, ...] 或 None(订阅全部)}
        self.consumers = dict(consumers or {})

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["name"],
            data["version"],
            data.get("fields", []),
            consumers=data.get("consumers"),
        )

    def field(self, name):
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def consumers_of(self, field_name):
        hit = []
        for consumer, fields in self.consumers.items():
            if fields is None or field_name in fields:
                hit.append(consumer)
        return sorted(hit)

    def validate(self, rows):
        """用真实样本校验，错误带 1 起始的行号。"""
        errors = []
        for line, row in enumerate(rows, start=1):
            for field in self.fields:
                value = row.get(field.name)
                if value is None:
                    if not field.nullable and field.default is None:
                        errors.append({
                            "line": line,
                            "field": field.name,
                            "message": "missing required field %r" % field.name,
                        })
                    continue
                check = _TYPE_CHECKS.get(field.type)
                if check is not None and not check(value):
                    errors.append({
                        "line": line,
                        "field": field.name,
                        "message": "expected %s, got %s" % (field.type, type(value).__name__),
                    })
                    continue
                if field.enum is not None and value not in field.enum:
                    errors.append({
                        "line": line,
                        "field": field.name,
                        "message": "value %r not in enum %s" % (value, field.enum),
                    })
        return errors


def _is_legacy(contract):
    return (
        isinstance(contract, dict)
        and "version" not in contract
        and all(isinstance(f, str) for f in contract.get("fields", []))
    )


class Registry:
    def __init__(self):
        self.contracts = {}          # 旧接口：name -> [字段名]
        self._versions = {}          # name -> {version: Contract}
        self._current = {}           # name -> version

    # ---- 注册与历史 ----

    def register(self, contract, allow_breaking=False):
        if _is_legacy(contract):
            self.contracts[contract["name"]] = list(contract.get("fields", []))
            return self.contracts[contract["name"]]
        if isinstance(contract, dict):
            contract = Contract.from_dict(contract)
        current = self.current(contract.name)
        if current is not None:
            report = self.diff(current, contract)
            if report["verdict"] == "breaking" and not allow_breaking:
                raise BreakingChangeError(report)
        self._versions.setdefault(contract.name, {})[contract.version] = contract
        self._current[contract.name] = contract.version
        return contract

    def current(self, name):
        version = self._current.get(name)
        return self._versions.get(name, {}).get(version)

    def history(self, name):
        return sorted(self._versions.get(name, {}))

    def rollback(self, name, version):
        versions = self._versions.get(name, {})
        if version not in versions:
            raise KeyError("unknown version %r for contract %r" % (version, name))
        self._current[name] = version
        return versions[version]

    def check(self, contract):
        """提交流程接入点：不注册，只返回与当前版本的兼容报告。"""
        if isinstance(contract, dict):
            contract = Contract.from_dict(contract)
        current = self.current(contract.name)
        if current is None:
            return {"compatible": [], "hints": [], "warnings": [], "breaking": [],
                    "verdict": "compatible"}
        return self.diff(current, contract)

    # ---- 查询 ----

    def validate(self, name, rows):
        contract = self.current(name)
        if contract is None:
            raise KeyError("contract %r not registered" % name)
        return contract.validate(rows)

    def for_consumer(self, consumer):
        """按消费方查询其当前订阅的契约、版本与字段。"""
        result = {}
        for name, version in self._current.items():
            contract = self._versions[name][version]
            if consumer not in contract.consumers:
                continue
            fields = contract.consumers[consumer]
            result[name] = {
                "version": version,
                "fields": list(fields) if fields is not None
                          else [f.name for f in contract.fields],
            }
        return result

    # ---- 比较 ----

    def diff(self, old, new):
        if isinstance(old, list) and isinstance(new, list):
            removed = [name for name in old if name not in new]
            added = [name for name in new if name not in old]
            return {"added": added, "removed": removed}
        return _diff_contracts(old, new)


def _diff_contracts(old, new):
    report = {"compatible": [], "hints": [], "warnings": [], "breaking": []}
    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}

    def breaking(field, change, detail=""):
        report["breaking"].append({
            "field": field,
            "change": change,
            "detail": detail,
            "consumers": old.consumers_of(field),
        })

    for name in old_fields:
        if name not in new_fields:
            breaking(name, "field_removed")

    for name, field in new_fields.items():
        if name not in old_fields:
            if not field.nullable and field.default is None:
                report["warnings"].append({
                    "field": name, "change": "required_field_added"})
            else:
                report["compatible"].append({"field": name, "change": "field_added"})

    for name in old_fields.keys() & new_fields.keys():
        o, n = old_fields[name], new_fields[name]
        if o.type != n.type:
            if (o.type, n.type) in _WIDENING:
                report["compatible"].append({
                    "field": name, "change": "type_widened",
                    "detail": "%s -> %s" % (o.type, n.type)})
            else:
                breaking(name, "type_tightened", "%s -> %s" % (o.type, n.type))
        if o.nullable and not n.nullable:
            breaking(name, "nullable_tightened")
        elif not o.nullable and n.nullable:
            report["compatible"].append({"field": name, "change": "nullable_relaxed"})
        if o.enum is None and n.enum is not None:
            breaking(name, "enum_imposed", "new constraint %s" % n.enum)
        elif o.enum is not None and n.enum is None:
            report["compatible"].append({"field": name, "change": "enum_removed"})
        elif o.enum is not None and n.enum is not None:
            removed = [v for v in o.enum if v not in n.enum]
            added = [v for v in n.enum if v not in o.enum]
            if removed:
                breaking(name, "enum_values_removed", "removed %s" % removed)
            if added:
                report["hints"].append({
                    "field": name, "change": "enum_values_added",
                    "detail": "added %s" % added})
        if o.default != n.default:
            report["hints"].append({
                "field": name, "change": "default_changed",
                "detail": "%r -> %r" % (o.default, n.default)})

    if report["breaking"]:
        report["verdict"] = "breaking"
    elif report["warnings"]:
        report["verdict"] = "warning"
    else:
        report["verdict"] = "compatible"
    return report

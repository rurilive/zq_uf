"""数据集质量规则模板层：公共模板 + 字段绑定 + 覆盖，纯标准库。

覆盖顺序（后者优先，按 (field, name) 匹配）：
    父模板 -> 子模板 -> bind_dataset 覆盖 -> apply 调用方规则
"""


class ConflictError(ValueError):
    """加载期规则冲突，携带模板、字段和来源。"""

    def __init__(self, template, field, source, message):
        self.template = template
        self.field = field
        self.source = source
        super().__init__(message)


class RuleTemplate:
    """公共规则模板，rules 使用逻辑字段名，parent 指向继承的模板。"""

    def __init__(self, name, version, rules, parent=None):
        self.name = name
        self.version = version
        self.rules = [dict(rule) for rule in rules]
        self.parent = parent


_TEMPLATES = {}  # name -> {version: RuleTemplate}
_BINDINGS = {}   # dataset -> {"template", "version", "fields", "overrides"}


def clear_registry():
    """清空模板和绑定，主要供测试隔离。"""
    _TEMPLATES.clear()
    _BINDINGS.clear()


def _key(rule):
    return (rule.get("field"), rule.get("name"))


def _check_duplicates(rules, template, source):
    seen = set()
    for rule in rules:
        key = _key(rule)
        if key in seen:
            raise ConflictError(
                template, key[0], source,
                "规则冲突: 模板=%s 字段=%s 规则=%s 来源=%s"
                % (template, key[0], key[1], source))
        seen.add(key)


def register_template(template):
    """注册模板，加载期校验模板自身重复规则。"""
    _check_duplicates(template.rules, template.name, "template:" + template.name)
    _TEMPLATES.setdefault(template.name, {})[template.version] = template
    return template


def _latest_version(name):
    versions = _TEMPLATES.get(name, {})
    if not versions:
        raise KeyError("未注册的模板: %s" % name)
    return max(versions)


def _resolve(template):
    """沿继承链从根到叶合并，子模板同 (field, name) 覆盖父模板。"""
    lineage = []
    node = template
    while node is not None:
        lineage.append(node)
        node = node.parent
    lineage.reverse()
    merged = {}
    order = []
    chain = []
    for node in lineage:
        source = "template:%s@%s" % (node.name, node.version)
        for rule in node.rules:
            key = _key(rule)
            action = "override" if key in merged else "define"
            if key not in merged:
                order.append(key)
            merged[key] = dict(rule)
            chain.append({"source": source, "field": key[0],
                          "name": key[1], "action": action})
    return [merged[key] for key in order], chain


def _apply_layer(base_rules, extra_rules, source, chain):
    """把一层规则按 (field, name) 合并到 base，记录覆盖链。"""
    merged = {_key(rule): dict(rule) for rule in base_rules}
    order = [_key(rule) for rule in base_rules]
    for rule in extra_rules:
        key = _key(rule)
        action = "override" if key in merged else "add"
        if key not in merged:
            order.append(key)
        merged[key] = dict(rule)
        chain.append({"source": source, "field": key[0],
                      "name": key[1], "action": action})
    return [merged[key] for key in order]


def _validate_overrides(base_rules, overrides, template, source):
    """加载期校验覆盖：自身重复、或缺 field 且无法按规则名唯一匹配即冲突。"""
    _check_duplicates(overrides, template, source)
    fields_by_name = {}
    for rule in base_rules:
        fields_by_name.setdefault(rule.get("name"), set()).add(rule.get("field"))
    for rule in overrides:
        if rule.get("field") is not None:
            continue
        fields = fields_by_name.get(rule.get("name"), set())
        if len(fields) != 1:
            raise ConflictError(
                template, rule.get("field"), source,
                "覆盖冲突: 模板=%s 规则=%s 来源=%s (缺少字段且无法唯一匹配)"
                % (template, rule.get("name"), source))
        rule["field"] = next(iter(fields))


def bind_dataset(dataset, template, fields=None, overrides=None, version=None):
    """把数据集绑定到模板：fields 为逻辑字段→实际字段，overrides 按
    (field, name) 覆盖模板规则；冲突在加载期抛 ConflictError。"""
    name = template if isinstance(template, str) else template.name
    if name not in _TEMPLATES:
        raise KeyError("未注册的模板: %s" % name)
    version = _latest_version(name) if version is None else version
    resolved, _ = _resolve(_TEMPLATES[name][version])
    overrides = [dict(rule) for rule in (overrides or [])]
    _validate_overrides(resolved, overrides, name, "dataset:" + dataset)
    _BINDINGS[dataset] = {
        "template": name,
        "version": version,
        "fields": dict(fields or {}),
        "overrides": overrides,
    }


def _bind_fields(rule, fields):
    bound = dict(rule)
    bound["field"] = fields.get(bound.get("field"), bound.get("field"))
    return bound


def apply(dataset, rules):
    """未绑定的数据集原样返回规则；已绑定的输出最终规则、覆盖链和迁移差异。"""
    binding = _BINDINGS.get(dataset)
    if binding is None:
        return {
            "dataset": dataset,
            "rules": [dict(rule) for rule in rules],
        }
    template = _TEMPLATES[binding["template"]][binding["version"]]
    resolved, chain = _resolve(template)
    merged = _apply_layer(resolved, binding["overrides"],
                          "dataset:" + dataset, chain)
    merged = _apply_layer(merged, rules, "call:" + dataset, chain)
    final = [_bind_fields(rule, binding["fields"]) for rule in merged]
    latest = _latest_version(binding["template"])
    migration = None
    if latest != binding["version"]:
        migration = diff_versions(binding["template"], binding["version"], latest)
    return {
        "dataset": dataset,
        "rules": final,
        "template": {"name": binding["template"], "version": binding["version"]},
        "override_chain": chain,
        "migration": migration,
    }


def diff_versions(template, from_version, to_version):
    """两个版本解析后规则的差异（逻辑字段视角）。"""
    old, _ = _resolve(_TEMPLATES[template][from_version])
    new, _ = _resolve(_TEMPLATES[template][to_version])
    old_map = {_key(rule): rule for rule in old}
    new_map = {_key(rule): rule for rule in new}
    return {
        "template": template,
        "from": from_version,
        "to": to_version,
        "added": [rule for key, rule in new_map.items() if key not in old_map],
        "removed": [rule for key, rule in old_map.items() if key not in new_map],
        "changed": [
            {"field": key[0], "name": key[1],
             "from": old_map[key], "to": new_map[key]}
            for key in old_map
            if key in new_map and old_map[key] != new_map[key]
        ],
    }


def _check(rule, row):
    value = row.get(rule.get("field"))
    name = rule.get("name")
    if name == "not_null":
        return value is not None and value != ""
    if name == "range":
        if value is None:
            return False
        if "min" in rule and value < rule["min"]:
            return False
        if "max" in rule and value > rule["max"]:
            return False
        return True
    if name == "allowed_values":
        return value in rule.get("values", [])
    return None


def backtest(dataset, rows):
    """用最终规则回测数据行（实际字段名），返回每条规则的通过/失败数。"""
    final = apply(dataset, [])["rules"]
    results = []
    for rule in final:
        passed = failed = 0
        unsupported = False
        for row in rows:
            outcome = _check(rule, row)
            if outcome is None:
                unsupported = True
            elif outcome:
                passed += 1
            else:
                failed += 1
        results.append({"field": rule.get("field"), "name": rule.get("name"),
                        "passed": passed, "failed": failed,
                        "unsupported": unsupported})
    return {"dataset": dataset, "rows": len(rows), "results": results}


def migrate_dataset(dataset, to_version, rows=None):
    """把绑定迁移到模板新版本，返回迁移差异；给 rows 时回测迁移影响。"""
    if dataset not in _BINDINGS:
        raise KeyError("未绑定的数据集: %s" % dataset)
    binding = _BINDINGS[dataset]
    from_version = binding["version"]
    before = backtest(dataset, rows) if rows is not None else None
    diff = diff_versions(binding["template"], from_version, to_version)
    binding["version"] = to_version
    result = {"dataset": dataset, "from": from_version,
              "to": to_version, "diff": diff}
    if rows is not None:
        after = backtest(dataset, rows)
        before_map = {(r["field"], r["name"]): r for r in before["results"]}
        impact = []
        for item in after["results"]:
            old = before_map.get((item["field"], item["name"]))
            impact.append({
                "field": item["field"], "name": item["name"],
                "before_failed": old["failed"] if old else None,
                "after_failed": item["failed"],
            })
        result["backtest"] = {"rows": len(rows), "impact": impact}
    return result

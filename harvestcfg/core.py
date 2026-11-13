"""采集配置中心：多层合并、校验、继承与热更新。"""
import copy
import difflib
import hashlib
import json
import os
import re
import time


class ConfigError(Exception):
    """配置错误基类。"""


class ValidationError(ConfigError):
    """字段或语法校验失败。"""


class SensitiveValueError(ValidationError):
    """敏感字段落了明文。"""


class ConflictError(ConfigError):
    """同层多个来源的同一字段取值冲突。"""


class CircularReferenceError(ConfigError):
    """extends 继承链出现环。"""


# 合并优先级从低到高：default < site < task < tenant。
LAYERS = ("default", "site", "task", "tenant")

_FIELD_TYPES = {
    "rate_limit": (int, float),
    "concurrency": int,
    "retry": dict,
    "headers": dict,
    "proxy_group": str,
    "selectors": dict,
    "patterns": dict,
    "api_key": str,
    "proxy_auth": str,
    "task": str,
    "tenant": str,
    "extends": str,
}

# 敏感字段：只允许引用（如 ${env:NAME}），禁止明文。
SENSITIVE_FIELDS = ("api_key", "proxy_auth")

_REF_RE = re.compile(r"^\$\{(env|vault|file):[A-Za-z0-9_./-]+\}$")

_RETRY_KEYS = {"max_attempts": int, "backoff": (int, float)}

# 这些字段在层间按键深合并，其余字段整体覆盖。
_MERGED_DICT_FIELDS = ("headers", "retry", "selectors", "patterns")


def load(site):
    with open(site, encoding="utf-8") as handle:
        return json.load(handle)


def _type_ok(value, expect):
    types = expect if isinstance(expect, tuple) else (expect,)
    if isinstance(value, bool) and bool not in types:
        return False
    return isinstance(value, types)


def _suggest(key, candidates):
    hints = difflib.get_close_matches(key, sorted(candidates), n=1, cutoff=0.5)
    return "，是否想写 %r？" % hints[0] if hints else ""


def _validate_selector(expr, field, source):
    if not isinstance(expr, str) or not expr.strip():
        raise ValidationError("选择器 %r 不能为空（来源 %s）" % (field, source))
    pairs = {"[": "]", "(": ")"}
    stack = []
    quote = None
    for ch in expr:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "])":
            if not stack or stack.pop() != ch:
                raise ValidationError(
                    "选择器 %r 语法错误：括号不配对（来源 %s）" % (expr, source))
    if quote or stack:
        raise ValidationError(
            "选择器 %r 语法错误：引号或括号未闭合（来源 %s）" % (expr, source))
    if expr.strip()[-1] in ">,+~":
        raise ValidationError(
            "选择器 %r 语法错误：以组合符结尾（来源 %s）" % (expr, source))


def validate_config(data, source="<memory>"):
    """校验单层配置，出错抛 ValidationError，消息带字段和来源。"""
    if not isinstance(data, dict):
        raise ValidationError("配置必须是 JSON 对象（来源 %s）" % source)
    for key, value in data.items():
        expect = _FIELD_TYPES.get(key)
        if expect is None:
            raise ValidationError(
                "未知字段 %r（来源 %s）%s" % (key, source, _suggest(key, _FIELD_TYPES)))
        if not _type_ok(value, expect):
            raise ValidationError(
                "字段 %r 类型错误：期望 %s（来源 %s）"
                % (key, expect, source))
        if key in SENSITIVE_FIELDS and not _REF_RE.match(value):
            raise SensitiveValueError(
                "敏感字段 %r 只允许引用（如 ${env:NAME}），禁止明文（来源 %s）"
                % (key, source))
        if key == "retry":
            for sub, sub_value in value.items():
                sub_expect = _RETRY_KEYS.get(sub)
                if sub_expect is None:
                    raise ValidationError(
                        "未知重试参数 %r（来源 %s）%s"
                        % (sub, source, _suggest(sub, _RETRY_KEYS)))
                if not _type_ok(sub_value, sub_expect):
                    raise ValidationError(
                        "重试参数 %r 类型错误（来源 %s）" % (sub, source))
        if key == "headers":
            for name, header in value.items():
                if not isinstance(name, str) or not isinstance(header, str):
                    raise ValidationError(
                        "请求头 %r 必须是字符串键值（来源 %s）" % (name, source))
        if key == "selectors":
            for name, expr in value.items():
                _validate_selector(expr, "selectors.%s" % name, source)
        if key == "patterns":
            for name, pattern in value.items():
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValidationError(
                        "正则 patterns.%s 语法错误：%s（来源 %s）"
                        % (name, exc, source))


def merge_layers(layers):
    """按 LAYERS 优先级合并 [(layer, data, source)]，返回 (config, provenance)。"""
    config = {}
    provenance = {}
    for layer, data, source in layers:
        for key, value in data.items():
            if key == "extends":
                continue
            if key in _MERGED_DICT_FIELDS and isinstance(config.get(key), dict):
                merged = dict(config[key])
                merged.update(value)
                config[key] = merged
            else:
                config[key] = copy.deepcopy(value)
            provenance.setdefault(key, []).append((layer, source))
    return config, provenance


def diff_configs(old, new):
    """扁平化比较两份配置，返回 (op, key, old, new) 列表。"""
    def flat(cfg, prefix, out):
        for key, value in cfg.items():
            full = "%s.%s" % (prefix, key) if prefix else key
            if isinstance(value, dict):
                flat(value, full, out)
            else:
                out[full] = value
    old_flat, new_flat = {}, {}
    flat(old, "", old_flat)
    flat(new, "", new_flat)
    changes = []
    for key in sorted(set(old_flat) | set(new_flat)):
        if key not in old_flat:
            changes.append(("added", key, None, new_flat[key]))
        elif key not in new_flat:
            changes.append(("removed", key, old_flat[key], None))
        elif old_flat[key] != new_flat[key]:
            changes.append(("changed", key, old_flat[key], new_flat[key]))
    return changes


class Resolved:
    """最终配置 + 逐项来源链。"""

    def __init__(self, config, provenance):
        self.config = config
        self.provenance = provenance

    def __getitem__(self, key):
        return self.config[key]

    def get(self, key, default=None):
        return self.config.get(key, default)

    def source_chain(self, field):
        """字段的来源链，按应用顺序返回 [(layer, source)]。"""
        return list(self.provenance.get(field, []))


class ConfigLoader:
    """四层配置加载器：default < site < task < tenant。"""

    def __init__(self):
        self.profiles = {}
        self._sources = {layer: [] for layer in LAYERS}

    def add_profile(self, name, data, source="<memory>"):
        validate_config(data, source)
        self.profiles[name] = data

    def set_layer(self, layer, data, source="<memory>"):
        self._check_layer(layer)
        validate_config(data, source)
        self._sources[layer] = [(data, source)]

    def add_source(self, layer, data, source="<memory>"):
        """同层追加来源；同字段取值不一致时报冲突（字段 + 双方来源）。"""
        self._check_layer(layer)
        validate_config(data, source)
        for old_data, old_source in self._sources[layer]:
            for key, value in data.items():
                if key in old_data and old_data[key] != value:
                    raise ConflictError(
                        "字段 %r 冲突：%s 定义为 %r，%s 定义为 %r"
                        % (key, old_source, old_data[key], source, value))
        self._sources[layer].append((data, source))

    def load_layer(self, layer, path):
        self.set_layer(layer, load(path), source=path)

    def resolve(self):
        ordered = []
        for layer in LAYERS:
            for data, source in self._sources[layer]:
                ordered.append((layer, self._resolve_extends(data, [], source), source))
        config, provenance = merge_layers(ordered)
        return Resolved(config, provenance)

    @staticmethod
    def _check_layer(layer):
        if layer not in LAYERS:
            raise ConfigError("未知层级 %r，可选：%s" % (layer, ", ".join(LAYERS)))

    def _resolve_extends(self, data, chain, source):
        name = data.get("extends")
        if not name:
            return data
        if name in chain:
            raise CircularReferenceError(
                "继承链出现循环：%s（来源 %s）" % (" -> ".join(chain + [name]), source))
        if name not in self.profiles:
            raise ValidationError(
                "extends 引用了不存在的 profile %r（来源 %s）" % (name, source))
        base = self._resolve_extends(self.profiles[name], chain + [name], source)
        merged = dict(base)
        merged.update({k: v for k, v in data.items() if k != "extends"})
        return merged


class Preview:
    """差异预览：先生成，再生效。"""

    def __init__(self, changes, fingerprint, applied=True, error=None):
        self.changes = changes
        self.fingerprint = fingerprint
        self.applied = applied
        self.error = error

    def __iter__(self):
        return iter(self.changes)

    def __bool__(self):
        return bool(self.changes)

    def __str__(self):
        if self.error:
            return "已拒绝：%s" % self.error
        marks = {"added": "+", "removed": "-", "changed": "~"}
        lines = []
        for op, key, old, new in self.changes:
            if op == "changed":
                lines.append("%s %s: %r -> %r" % (marks[op], key, old, new))
            elif op == "added":
                lines.append("%s %s: %r" % (marks[op], key, new))
            else:
                lines.append("%s %s: %r" % (marks[op], key, old))
        return "\n".join(lines) if lines else "（无变化）"


def _fingerprint(files):
    """配置指纹：按相对路径 + 内容哈希，只看真实变化。"""
    digest = hashlib.sha256()
    for rel, path in sorted(files):
        digest.update(rel.encode("utf-8"))
        with open(path, "rb") as handle:
            digest.update(handle.read())
    return digest.hexdigest()


class Watcher:
    """热更新：指纹判断变化，先差异预览再生效，校验失败自动回滚。"""

    def __init__(self, path):
        self.path = path
        self.loader = ConfigLoader()
        self._resolved = Resolved({}, {})
        self._fingerprint = None
        self._history = []
        self.versions = []
        self.last_preview = None
        self.check_once()

    @property
    def resolved(self):
        return self._resolved

    def _scan(self):
        files = []
        for layer in LAYERS:
            path = os.path.join(self.path, layer + ".json")
            if os.path.exists(path):
                files.append((layer + ".json", path))
        profiles_dir = os.path.join(self.path, "profiles")
        if os.path.isdir(profiles_dir):
            for name in sorted(os.listdir(profiles_dir)):
                if name.endswith(".json"):
                    files.append(("profiles/" + name, os.path.join(profiles_dir, name)))
        return files

    def _record(self, fingerprint, status, changes, error=None):
        self.versions.append({
            "version": len(self.versions) + 1,
            "fingerprint": fingerprint,
            "time": time.time(),
            "status": status,
            "diff": list(changes),
            "error": str(error) if error else None,
        })

    def check_once(self):
        """检查一次；指纹不变返回 None，否则返回 Preview。"""
        files = self._scan()
        fingerprint = _fingerprint(files)
        if fingerprint == self._fingerprint:
            return None
        candidate = ConfigLoader()
        try:
            for rel, path in files:
                data = load(path)
                if rel.startswith("profiles/"):
                    candidate.add_profile(os.path.splitext(os.path.basename(rel))[0],
                                          data, source=path)
                else:
                    candidate.add_source(os.path.splitext(rel)[0], data, source=path)
            resolved = candidate.resolve()
        except ConfigError as exc:
            # 校验失败：不切换，保持上一份可用配置（自动回滚语义）。
            self._record(fingerprint, "rejected", [], error=exc)
            self.last_preview = Preview([], fingerprint, applied=False, error=exc)
            return self.last_preview
        changes = diff_configs(self._resolved.config, resolved.config)
        self._history.append((self._fingerprint, self.loader, self._resolved))
        self.loader, self._resolved, self._fingerprint = candidate, resolved, fingerprint
        self._record(fingerprint, "applied", changes)
        self.last_preview = Preview(changes, fingerprint)
        return self.last_preview

    def preview(self):
        return self.last_preview

    def rollback(self):
        """回滚到上一份生效的配置，返回差异预览。"""
        if not self._history:
            raise ConfigError("没有更早的版本可回滚")
        fingerprint, loader, resolved = self._history.pop()
        changes = diff_configs(self._resolved.config, resolved.config)
        self.loader, self._resolved, self._fingerprint = loader, resolved, fingerprint
        self._record(fingerprint, "rollback", changes)
        self.last_preview = Preview(changes, fingerprint)
        return self.last_preview


def watch(path):
    """监听配置目录（default/site/task/tenant.json + profiles/），返回 Watcher。"""
    return Watcher(path)

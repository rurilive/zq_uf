"""Rule indexing and combined condition matching."""

from .normalize import canonical_text, normalize_path


class _PathTrie:
    def __init__(self):
        self._children = {}
        self._rules = []

    def insert(self, segments, rule):
        node = self
        for segment in segments:
            node = node._children.setdefault(segment, _PathTrie())
        node._rules.append(rule)

    def match(self, path):
        node = self
        matched = list(node._rules)
        for segment in path.split("/")[1:]:
            if not segment:
                continue
            if segment not in node._children:
                break
            node = node._children[segment]
            matched.extend(node._rules)
        return matched


class RuleIndex:
    """Rules bucketed by method and indexed by canonical path prefix."""

    def __init__(self, rules):
        self._methods = {}
        for order, rule in enumerate(rules):
            entry = (order, rule)
            method = (rule.method or "").upper()
            trie = self._methods.setdefault(method, _PathTrie())
            prefix = normalize_path(canonical_text(rule.path_prefix)) if rule.path_prefix else "/"
            segments = [segment for segment in prefix.split("/") if segment]
            trie.insert(segments, entry)

    def candidates(self, method, path):
        ordered = []
        seen = set()
        buckets = (self._methods.get(method.upper()), self._methods.get(""))
        for trie in buckets:
            if trie is None:
                continue
            for entry in trie.match(path):
                order = entry[0]
                if order not in seen:
                    seen.add(order)
                    ordered.append(entry)
        ordered.sort(key=lambda entry: entry[0])
        return [rule for _, rule in ordered]


def _needle_hit(needle, text):
    return bool(needle) and canonical_text(needle) in text


def evaluate_rule(rule, view):
    """Return evidence fragments when every rule condition matches."""
    fragments = []

    if rule.header:
        wanted_name, _, wanted_value = rule.header.partition(":")
        wanted_name = wanted_name.strip().lower()
        wanted_value = canonical_text(wanted_value.strip())
        hit = None
        for header in view.headers:
            if header["name"] != wanted_name:
                continue
            if not wanted_value or wanted_value in header["canonical_value"]:
                hit = header
                break
        if hit is None:
            return None
        fragments.append({
            "location": "header",
            "field": hit["name"],
            "original": hit["original_value"][:120],
            "normalized": hit["canonical_value"][:120],
        })

    if rule.query_param:
        wanted_name, _, wanted_value = rule.query_param.partition("=")
        wanted_name = canonical_text(wanted_name)
        wanted_value = canonical_text(wanted_value)
        hit = None
        for entry in view.url.query:
            if entry.canonical_name != wanted_name:
                continue
            if not wanted_value or wanted_value in entry.canonical_value:
                hit = entry
                break
        if hit is None:
            return None
        fragments.append({
            "location": "query",
            "field": hit.name,
            "index": hit.index,
            "original": hit.value[:120],
            "normalized": (
                hit.canonical_name + "=" + hit.canonical_value
            )[:120],
        })

    if rule.body_field:
        wanted_name, _, wanted_value = rule.body_field.partition("=")
        wanted_name = canonical_text(wanted_name)
        wanted_value = canonical_text(wanted_value)
        hit = None
        for leaf in view.body.leaves:
            names = view.body_field_names.get(id(leaf), ())
            if wanted_name not in names:
                continue
            if not wanted_value or wanted_value in leaf.canonical:
                hit = leaf
                break
        if hit is None:
            return None
        fragments.append({
            "location": "body",
            "field": hit.field,
            "original": hit.text[:120],
            "normalized": hit.canonical[:120],
        })

    if rule.needle:
        scope_hits = []
        for scope in view.scan_scopes:
            if _needle_hit(rule.needle, scope["text"]):
                scope_hits.append({
                    "location": scope["location"],
                    "original": scope["original"][:120],
                    "normalized": scope["text"][:120],
                })
        if not scope_hits:
            return None
        fragments.extend(scope_hits[:4])

    if not fragments:
        fragments.append({
            "location": "path",
            "original": view.url.original,
            "normalized": view.url.canonical,
        })
    return fragments

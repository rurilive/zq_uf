"""语料收敛，当前没有覆盖数据可以比较。

corpus 是内存里的条目列表，条目至少带 id，可选 project 和 size。
"""

_corpus = []


def _check(corpus):
    if not isinstance(corpus, list):
        raise ValueError("corpus must be a list")
    for item in corpus:
        if not isinstance(item, dict):
            raise ValueError("corpus item must be a dict")
        if "id" not in item:
            raise ValueError("corpus item needs id")


def minimize(corpus):
    """没有覆盖贡献，只能原样保留全部语料。"""
    global _corpus
    _check(corpus)
    _corpus = list(corpus)
    return {
        "project": corpus[0].get("project") if corpus else None,
        "kept": [item["id"] for item in corpus],
        "dropped": [],
        "coverage_before": 0,
        "coverage_after": 0,
        "archive": {},
    }


def add(items):
    """新语料直接进集合，不评估它带来多少覆盖。"""
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            raise ValueError("item needs id")
    _corpus.extend(items)
    return {"added": len(items), "total": len(_corpus)}


def report():
    """只按项目数份数，不区分覆盖贡献。"""
    projects = {}
    size = 0
    for item in _corpus:
        project = item.get("project", "default")
        projects[project] = projects.get(project, 0) + 1
        size += item.get("size", 0)
    return {
        "projects": projects,
        "total": len(_corpus),
        "size": size,
        "regions": 0,
    }

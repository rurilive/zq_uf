"""用例集加载。

用例按业务线分开维护，支持两种形态：
1. 单文件：search.json，内容为用例数组，文件名（去扩展名）作为缺省业务线；
2. 目录：cases/ 下放多个 <biz>.json，每个文件对应一条业务线，
   改动只影响对应业务线，不同业务线互不干扰。

用例里显式写了 biz 时以用例为准，否则用文件名补全，保证每条用例都归属业务线。
"""
import json
import os
from typing import Iterable, List

from .errors import DuplicateCaseId, WafValidationError
from .models import Case


def _load_json_file(path: str) -> List[Case]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise WafValidationError(f"invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise WafValidationError(f"cannot read case file {path}: {exc}") from exc

    if not isinstance(data, list):
        raise WafValidationError(f"{path}: case file must contain a JSON array")

    fallback_biz = os.path.splitext(os.path.basename(path))[0]
    cases = []
    for item in data:
        if not isinstance(item, dict):
            raise WafValidationError(f"{path}: every case must be an object")
        if "biz" not in item or not item.get("biz"):
            item = {**item, "biz": fallback_biz}
        cases.append(Case.from_dict(item))
    return cases


def load_case_files(paths: Iterable[str]) -> List[Case]:
    """从一组文件/目录加载用例，校验全局 id 唯一。"""
    collected: List[Case] = []
    seen = set()
    visited_files = set()

    def _add(cases: List[Case]):
        for case in cases:
            if case.id in seen:
                raise DuplicateCaseId(case.id)
            seen.add(case.id)
            collected.append(case)

    for path in paths:
        if not isinstance(path, str) or not path:
            raise WafValidationError("case path must be a non-empty string")
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if not name.endswith(".json"):
                    continue
                file_path = os.path.join(path, name)
                if file_path in visited_files:
                    continue
                visited_files.add(file_path)
                _add(_load_json_file(file_path))
        elif os.path.isfile(path):
            if path in visited_files:
                continue
            visited_files.add(path)
            _add(_load_json_file(path))
        else:
            raise WafValidationError(f"case path does not exist: {path}")

    return collected


def group_by_biz(cases: Iterable[Case]) -> dict:
    """按业务线分组，方便安全同学只维护/运行自己业务线的用例。"""
    grouped = {}
    for case in cases:
        grouped.setdefault(case.biz, []).append(case)
    return grouped

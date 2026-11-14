"""带版本保护与幂等键的写入模块。

口径：
- 每行可带 ``version``（整数）；已存在的行只能被更高版本覆盖，
  相等或更低版本记为冲突，整批回滚。
- 不带 ``version`` 的行沿用旧行为（按主键直接覆盖），用于兼容；
  存量行没有版本基线时，带版本写入直接接管并建立基线。
- 同一 ``idem_key`` 的重复提交直接返回首次结果，不再写入。
- 批量写先整批校验再落库，任一行冲突则全部不写（原子性）。
"""

import copy


class IdemWriteError(ValueError):
    """入参非法等异常路径抛出的明确错误。"""


_rows = {}
_versions = {}
_idem_store = {}


def reset():
    """清空全部行、版本与幂等回放记录（主要给测试用）。"""
    _rows.clear()
    _versions.clear()
    _idem_store.clear()


def _snapshot():
    return {
        "rows": copy.deepcopy(_rows),
        "versions": copy.deepcopy(_versions),
    }


def _restore(snapshot):
    _rows.clear()
    _rows.update(copy.deepcopy(snapshot["rows"]))
    _versions.clear()
    _versions.update(copy.deepcopy(snapshot["versions"]))


def _validate(rows, idem_key):
    if idem_key is not None and not isinstance(idem_key, str):
        raise IdemWriteError("idem_key 必须是字符串或 None")
    try:
        materialized = list(rows)
    except TypeError as exc:
        raise IdemWriteError("rows 必须是可迭代的行字典列表") from exc
    for index, row in enumerate(materialized):
        if not isinstance(row, dict):
            raise IdemWriteError("第 %d 行不是字典: %r" % (index, row))
        if "key" not in row:
            raise IdemWriteError("第 %d 行缺少主键 'key'" % index)
        version = row.get("version")
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int)
        ):
            raise IdemWriteError(
                "第 %d 行的 version 必须是整数或 None，实际为 %r"
                % (index, version)
            )
    return materialized


def _conflict(row, reason, stored_version=None):
    return {
        "key": row["key"],
        "reason": reason,
        "incoming": copy.deepcopy(row),
        "stored": {
            "version": stored_version,
            "row": copy.deepcopy(_rows.get(row["key"])),
        },
    }


def _prepare(materialized):
    updates = []
    conflicts = []
    seen = set()
    for row in materialized:
        key = row["key"]
        if key in seen:
            conflicts.append(
                _conflict(row, "同一批次内主键重复")
            )
            continue
        seen.add(key)
        incoming_version = row.get("version")
        stored_version = _versions.get(key)
        if (
            key in _rows
            and incoming_version is not None
            and stored_version is not None
            and incoming_version <= stored_version
        ):
            conflicts.append(
                _conflict(
                    row,
                    "版本冲突: incoming=%s stored=%s，旧版本不能覆盖新版本"
                    % (incoming_version, stored_version),
                    stored_version,
                )
            )
            continue
        updates.append((key, row, incoming_version))
    return updates, conflicts


def _apply(updates):
    for key, row, version in updates:
        _rows[key] = copy.deepcopy(row)
        if version is None:
            _versions.pop(key, None)
        else:
            _versions[key] = version


def write(rows, idem_key=None):
    """带版本和幂等键的批量写入。

    返回字典字段：
    - written: 实际写入行数（回滚或幂等重放时为 0）
    - idem_hits: 幂等命中次数（重放返回首次结果时为 1）
    - conflicts: 冲突行列表，每条保留 incoming/stored 两侧值与 reason
    - rolled_back: 回滚行数（整批失败时等于提交行数）
    - rows: 当前存储快照（深拷贝）
    """
    materialized = _validate(rows, idem_key)

    if idem_key is not None and idem_key in _idem_store:
        result = copy.deepcopy(_idem_store[idem_key])
        result["idem_hits"] = 1
        return result

    updates, conflicts = _prepare(materialized)
    if conflicts:
        result = {
            "written": 0,
            "idem_hits": 0,
            "conflicts": conflicts,
            "rolled_back": len(materialized),
            "rows": copy.deepcopy(_rows),
        }
    else:
        snapshot = _snapshot()
        try:
            _apply(updates)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            _restore(snapshot)
            raise IdemWriteError("写入过程中出错，已回滚: %s" % exc) from exc
        result = {
            "written": len(updates),
            "idem_hits": 0,
            "conflicts": [],
            "rolled_back": 0,
            "rows": copy.deepcopy(_rows),
        }

    if idem_key is not None:
        _idem_store[idem_key] = copy.deepcopy(result)
    return copy.deepcopy(result)

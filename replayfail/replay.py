"""失败样本记录与回归回放。

样本以 JSONL 保存完整请求（方法、URL、请求头、请求体）和响应摘要；
回放时按原请求重建调用，对比新旧错误类型，并把结果幂等写入独立的
SQLite 演练库，不修改样本文件或任何正式数据。
"""

import hashlib
import inspect
import json
import os
import sqlite3
from datetime import datetime, timezone

BODY_SUMMARY_LIMIT = 500
DEFAULT_DRILL_DB = os.path.join(".replayfail", "drill.sqlite3")


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_headers(headers):
    if not headers:
        return {}
    items = headers.items() if isinstance(headers, dict) else headers
    return {str(key): str(value) for key, value in items}


def _normalize_body(body):
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)


def _summarize_response(response):
    if not response:
        return None
    if not isinstance(response, dict):
        return None
    body = response.get("body_summary", response.get("body", "")) or ""
    if not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False)
    if len(body) > BODY_SUMMARY_LIMIT:
        body = body[:BODY_SUMMARY_LIMIT] + "...<truncated>"
    return {
        "status": response.get("status"),
        "headers": _normalize_headers(response.get("headers")),
        "body_summary": body,
    }


def _sample_id(method, url, headers, body):
    identity = json.dumps(
        [method, url, headers, body], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()


def _normalize_sample(failure):
    method = str(failure.get("method", "GET")).upper()
    url = failure.get("url", "")
    headers = _normalize_headers(failure.get("headers"))
    body = _normalize_body(failure.get("body"))
    response = failure.get("response")
    if response is None and "status" in failure:
        response = {
            "status": failure.get("status"),
            "headers": failure.get("response_headers"),
            "body_summary": failure.get("response_body"),
        }
    return {
        "id": _sample_id(method, url, headers, body),
        "method": method,
        "url": url,
        "headers": headers,
        "body": body,
        "error_type": failure.get("error_type") or failure.get("error") or "unknown",
        "response": _summarize_response(response),
        "recorded_at": _utc_now(),
    }


def record(failure, path):
    """把一次失败请求规范化后追加为一行 JSONL 样本。"""
    sample = _normalize_sample(failure)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample, ensure_ascii=False) + "\n")


class ReplayResult(dict):
    """三分类计数；与普通 dict 比较时按对方拥有的键做子集匹配。"""

    def __eq__(self, other):
        if isinstance(other, ReplayResult):
            return dict.__eq__(self, other)
        if isinstance(other, dict):
            return all(self.get(key) == value for key, value in other.items())
        return NotImplemented

    def __ne__(self, other):
        compared = self.__eq__(other)
        if compared is NotImplemented:
            return compared
        return not compared


def _invoke_fetch(fetch, request):
    """兼容旧的单参数 fetch(url)，新签名 fetch(request) 收到完整请求。"""
    parameters = list(inspect.signature(fetch).parameters.values())
    if len(parameters) == 1 and parameters[0].name == "url":
        return fetch(request["url"])
    return fetch(request)


def _classify(old_error, outcome):
    if isinstance(outcome, str):
        new_error = outcome
    elif isinstance(outcome, dict):
        new_error = str(outcome.get("error_type") or "unknown")
    elif outcome is None or outcome:
        return "fixed", None
    else:
        return "still_failed", old_error
    if new_error == old_error:
        return "still_failed", new_error
    return "error_changed", new_error


def _open_drill_db(drill_db):
    directory = os.path.dirname(drill_db)
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(drill_db)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS replay_results (
            sample_id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            method TEXT,
            url TEXT,
            old_error_type TEXT,
            new_error_type TEXT,
            category TEXT NOT NULL,
            replay_count INTEGER NOT NULL DEFAULT 1,
            first_replayed_at TEXT NOT NULL,
            last_replayed_at TEXT NOT NULL
        )
        """
    )
    return connection


def replay(paths, fetch, drill_db=DEFAULT_DRILL_DB):
    """回放 paths 中的失败样本，返回三分类计数并幂等写入演练库。"""
    counts = ReplayResult(fixed=0, still_failed=0, error_changed=0)
    connection = _open_drill_db(drill_db)
    try:
        with connection:
            for path in paths:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        sample = json.loads(line)
                        request = {
                            "method": sample.get("method", "GET"),
                            "url": sample.get("url", ""),
                            "headers": sample.get("headers", {}),
                            "body": sample.get("body", ""),
                        }
                        old_error = sample.get("error_type", "unknown")
                        try:
                            outcome = _invoke_fetch(fetch, request)
                        except Exception as exc:
                            outcome = type(exc).__name__
                        category, new_error = _classify(old_error, outcome)
                        counts[category] += 1
                        now = _utc_now()
                        connection.execute(
                            """
                            INSERT INTO replay_results (
                                sample_id, source_file, method, url,
                                old_error_type, new_error_type, category,
                                replay_count, first_replayed_at, last_replayed_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                            ON CONFLICT(sample_id) DO UPDATE SET
                                source_file=excluded.source_file,
                                method=excluded.method,
                                url=excluded.url,
                                old_error_type=excluded.old_error_type,
                                new_error_type=excluded.new_error_type,
                                category=excluded.category,
                                replay_count=replay_results.replay_count + 1,
                                last_replayed_at=excluded.last_replayed_at
                            """,
                            (
                                sample.get("id") or _sample_id(
                                    request["method"], request["url"],
                                    request["headers"], request["body"],
                                ),
                                path,
                                request["method"],
                                request["url"],
                                old_error,
                                new_error,
                                category,
                                now,
                                now,
                            ),
                        )
    finally:
        connection.close()
    return counts

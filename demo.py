"""采集响应校验与降级演示：正常结构与坏结构两组。"""

import json

from jsonschema2 import repair, validate

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "importance": "critical"},
        "title": {"type": "string", "default": "untitled", "importance": "important"},
        "status": {"type": "string", "enum": ["ok", "error"], "importance": "important"},
        "tags": {"type": "array", "items": {"type": "string"}, "importance": "optional"},
        "meta": {
            "type": "object",
            "importance": "optional",
            "properties": {
                "author": {"type": "string", "default": "anonymous", "importance": "important"},
                "score": {"type": "number", "default": 0, "importance": "optional"},
            },
        },
    },
    "required": ["id", "title"],
}

NORMAL = {
    "id": 7,
    "title": "采集正常",
    "status": "ok",
    "tags": ["news", "sports"],
    "meta": {"author": "alice", "score": 9.5},
}

BAD = {
    "id": 8,
    "status": "unknown",          # 枚举外 -> important 降级为 "ok"
    "tags": ["news", 42],         # 数组元素类型错 -> optional 丢弃该元素
    "meta": {"author": 100, "score": "high"},  # author 降级，score 用默认值
}

CRITICAL = {
    "id": "eight",                # critical 类型错 -> 整条隔离
    "title": "坏主键",
}


def show(name, payload):
    print("=" * 60)
    print(name)
    print("-" * 60)
    report = validate(payload, SCHEMA)
    print("status  :", report["status"])
    print("errors  :", json.dumps(report["errors"], ensure_ascii=False))
    print("degraded:", json.dumps(report["degraded"], ensure_ascii=False))
    print("isolated:", json.dumps(report["isolated"], ensure_ascii=False))
    print("payload :", json.dumps(report["payload"], ensure_ascii=False))


def main():
    show("正常结构", NORMAL)
    show("坏结构（可降级）", BAD)
    show("坏结构（不可修复）", CRITICAL)

    print("=" * 60)
    print("repair() 降级入库结果")
    print("-" * 60)
    print(json.dumps(repair(BAD, SCHEMA), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

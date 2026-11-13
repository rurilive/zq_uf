"""解析 demo.log（混合两种格式、堆栈、时区和坏行）并打印结果。"""

import json

from logparse import parse_result


def main():
    with open("demo.log", encoding="utf-8") as fh:
        result = parse_result(fh.read().splitlines())

    print("== records ==")
    for record in result.records:
        print(json.dumps(record, ensure_ascii=False))

    print("== failures ==")
    for failure in result.failures:
        print(json.dumps(failure, ensure_ascii=False))

    print("== stats ==")
    print(json.dumps(result.stats, ensure_ascii=False))


if __name__ == "__main__":
    main()

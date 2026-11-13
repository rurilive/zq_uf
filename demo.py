"""演示：配置驱动签名，输出与 tests/test_sign.py 的断言一致。"""
from signparam import sign

RULE = {
    "sort": "asc",
    "quote": True,
    "algo": "hmac-sha256",
    "secret": "demo-secret",
    "timestamp": {"param": "ts", "format": "epoch"},
    "nonce": {"param": "nonce", "length": 16},
}

PARAMS = {
    "appid": "demo",
    "q": "键盘",
    "ts": "1700000000",
    "nonce": "abcdef1234567890",
}

EXPECTED_SIGN = "61030f9b7e46aaf56c1955863add59579d748cccc444267e87e7f0a449642740"


def main():
    result = sign(PARAMS, RULE)
    print("raw:", result["raw"])
    print("sign:", result["sign"])
    print("snapshot:", result["snapshot"])

    replay = sign(result["snapshot"], RULE)
    assert replay["sign"] == result["sign"], "回放签名不一致"
    assert result["sign"] == EXPECTED_SIGN, "签名与 tests 断言不一致"
    print("replay ok, 与 tests 断言一致")

    # 缺省规则（md5 + 升序拼接），与旧行为一致
    print("default:", sign({"appid": "demo", "ts": "1700000000", "q": "键盘"})["sign"])


if __name__ == "__main__":
    main()

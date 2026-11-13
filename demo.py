from langdetect import detect

SAMPLES = {
    "zh": "这款机械键盘手感很好，包邮到家，价格也不贵。",
    "en": "The mechanical keyboard is on sale and the price is very good.",
    "ja": "このキーボードはとても静かです。価格も安いです。",
    "ko": "이 키보드는 정말 좋아요. 배송도 빠르고 가격도 저렴합니다.",
    "fr": "Le clavier mécanique est très bon et le prix est bas.",
    "de": "Die mechanische Tastatur ist sehr gut und der Versand ist kostenlos.",
}

MIXED_PAGE = """机械键盘特价，包邮到家。

This mechanical keyboard is on sale now.

このキーボードはとても静かです。"""

for expected, text in SAMPLES.items():
    result = detect(text)
    print(f"[{expected}] -> {result['lang']} "
          f"confidence={result['confidence']} low={result['low_confidence']}")

print("\n混合页面:")
result = detect(MIXED_PAGE)
print(f"主导语言={result['lang']} mixed={result['mixed']}")
for lang, info in result["distribution"].items():
    print(f"  {lang}: 段落数={info['paragraphs']} 置信度={info['confidence']}")
for segment in result["segments"]:
    print(f"  [{segment['lang']}] {segment['confidence']:.2f} {segment['text']}")

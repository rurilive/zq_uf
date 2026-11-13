"""按页码翻商品分页接口的最小实现。"""
from dataclasses import dataclass


@dataclass
class Item:
    item_id: str
    title: str
    price: str
    url: str


def parse_page(payload):
    items = []
    for raw in payload.get("items") or []:
        items.append(
            Item(
                item_id=str(raw.get("id", "")),
                title=raw.get("title", ""),
                price=raw.get("price", ""),
                url=raw.get("url", ""),
            )
        )
    return items


def fetch_all(fetch_page):
    page = 1
    collected = []
    while True:
        items = parse_page(fetch_page(page))
        if not items:
            break
        collected.extend(items)
        page += 1
    return collected

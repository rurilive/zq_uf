"""sitemap 入口读取的最小实现。"""
import xml.etree.ElementTree as ET


def parse_urlset(xml_text):
    root = ET.fromstring(xml_text)
    entries = []
    for node in root.findall("url"):
        entries.append(
            {
                "loc": node.findtext("loc") or "",
                "lastmod": node.findtext("lastmod") or "",
                "changefreq": node.findtext("changefreq") or "",
            }
        )
    return entries


def load_sources(paths):
    entries = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            entries.extend(parse_urlset(handle.read()))
    return entries

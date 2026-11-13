"""两个站点的端到端演示：索引递归、gz 流式解析、robots 排除、增量落盘。"""
import gzip
import os

from sitemaptool import load_sources, refresh_all

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")
OUT = os.path.join(BASE, "output")


def write(path, content, gzipped=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = content.encode("utf-8")
    with gzip.open(path, "wb") if gzipped else open(path, "wb") as handle:
        handle.write(data)


def build_fixtures():
    write(
        os.path.join(BASE, "shop_index.xml"),
        """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>shop/products.xml.gz</loc></sitemap>
  <sitemap><loc>news/news_index.xml</loc></sitemap>
</sitemapindex>\n""",
    )
    write(
        os.path.join(BASE, "shop", "products.xml.gz"),
        """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://shop.example/p1</loc><lastmod>2026-06-10</lastmod>
    <changefreq>daily</changefreq></url>
  <url><loc>http://shop.example/p2</loc><lastmod>2025-01-15</lastmod>
    <changefreq>yearly</changefreq></url>
  <url><loc>http://shop.example/p1</loc><lastmod>2026-06-10</lastmod>
    <changefreq>daily</changefreq></url>
  <url><loc>http://shop.example/private/admin</loc></url>
</urlset>\n""",
        gzipped=True,
    )
    write(
        os.path.join(BASE, "news", "news_index.xml"),
        """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>latest.xml</loc></sitemap>
</sitemapindex>\n""",
    )
    write(
        os.path.join(BASE, "news", "latest.xml"),
        """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://news.example/n1</loc><lastmod>2026-06-12</lastmod>
    <changefreq>hourly</changefreq></url>
</urlset>\n""",
    )
    write(
        os.path.join(BASE, "broken.xml"),
        "<?xml version=\"1.0\"?><urlset><url><loc>oops</loc>",
    )
    write(
        os.path.join(BASE, "shop_robots.txt"),
        "User-agent: *\nDisallow: /private/\n",
    )


def main():
    build_fixtures()
    sources = [
        os.path.join(BASE, "shop_index.xml"),
        os.path.join(BASE, "news", "news_index.xml"),
        os.path.join(BASE, "broken.xml"),
    ]
    entries = load_sources(sources)
    print(f"递归发现 {len(entries)} 条原始链接（含重复、含待排除项）")

    result = refresh_all(
        entries,
        OUT,
        robots_rules={"shop.example": os.path.join(BASE, "shop_robots.txt")},
    )
    for site, report in result["reports"].items():
        print(f"站点 {site}: 写入={report['written']} 条目={report['total']} -> {report['path']}")
    for item in result["excluded"]:
        print(f"robots 排除: {item['loc']}（{item['reason']}）")

    again = refresh_all(
        entries,
        OUT,
        robots_rules={"shop.example": os.path.join(BASE, "shop_robots.txt")},
    )
    for site, report in again["reports"].items():
        print(f"增量复跑 {site}: written={report['written']}（无变化不重写）")


if __name__ == "__main__":
    main()

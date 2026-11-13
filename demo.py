from sitemaptool import parse_urlset

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>http://site/a</loc><lastmod>2026-03-01</lastmod></url>
  <url><loc>http://site/b</loc><changefreq>daily</changefreq></url>
</urlset>
"""

for entry in parse_urlset(SAMPLE):
    print(entry["loc"], entry["lastmod"], entry["changefreq"])

# TODO: 第二段演示（实现后补）

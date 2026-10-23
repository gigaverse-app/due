"""Check the rendered publication contract, including canonical URLs and search."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = []
        self.description = []
        self.social = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical.append(values.get("href", ""))
        if tag == "meta" and values.get("name") == "description":
            self.description.append(values.get("content", ""))
        if tag == "meta" and values.get("property") == "og:url":
            self.social.append(values.get("content", ""))


def main():
    root = Path(__file__).resolve().parents[1]
    site = root / "site"
    canonical_urls = set()
    pages = list(site.rglob("index.html"))
    assert pages, "Build the site before validating it"
    for page in pages:
        metadata = Metadata()
        metadata.feed(page.read_text(encoding="utf-8"))
        assert len(metadata.canonical) == 1, f"{page}: expected one canonical URL"
        canonical = metadata.canonical[0]
        parsed = urlsplit(canonical)
        assert parsed.scheme == "https" and parsed.netloc, f"{page}: invalid canonical"
        assert canonical not in canonical_urls, f"{page}: duplicate canonical"
        canonical_urls.add(canonical)
        assert metadata.description and metadata.description[0].strip(), f"{page}: missing description"
        assert metadata.social == [canonical], f"{page}: inconsistent social URL"
    sitemap = ElementTree.parse(site / "sitemap.xml")
    locations = {node.text for node in sitemap.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
    assert canonical_urls <= locations, "Published pages missing from sitemap"
    search = json.loads((site / "search/search_index.json").read_text(encoding="utf-8"))
    assert len(search["docs"]) >= len(pages), "Search index does not cover the published pages"
    assert not any("/work-in-progress/" in url or "/plans/" in url for url in locations)
    print(f"Validated {len(pages)} pages: canonical, descriptions, social metadata, sitemap, search")


if __name__ == "__main__":
    main()

"""Check the rendered publication contract, including canonical URLs and search."""

import json
import struct
from hashlib import sha256
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
        self.social_images = []
        self.icons = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "canonical":
            self.canonical.append(values.get("href", ""))
        if tag == "meta" and values.get("name") == "description":
            self.description.append(values.get("content", ""))
        if tag == "link" and values.get("rel") == "icon":
            self.icons.append(values.get("href", ""))
        if tag == "meta" and values.get("property") == "og:image":
            self.social_images.append(values.get("content", ""))
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
        assert len(metadata.social_images) == 1, f"{page}: missing social image"
        image_url = urlsplit(metadata.social_images[0])
        assert image_url.scheme == "https" and image_url.netloc == parsed.netloc
        assert image_url.path.endswith("/assets/social-preview.png")
        expected_social_version = sha256((site / "assets/social-preview.png").read_bytes()).hexdigest()[:12]
        assert image_url.query == f"v={expected_social_version}", f"{page}: stale social card"
        assert len(metadata.icons) == 1, f"{page}: missing favicon"
        expected_icon_version = sha256((site / "assets/icon.png").read_bytes()).hexdigest()[:12]
        assert urlsplit(metadata.icons[0]).query == f"v={expected_icon_version}", f"{page}: stale favicon"
        assert (page.parent / urlsplit(metadata.icons[0]).path).resolve() == (site / "assets/icon.png").resolve()
    for name, size in (("icon.png", (256, 256)), ("social-preview.png", (1280, 640))):
        image = (site / "assets" / name).read_bytes()
        assert image[:8] == b"\x89PNG\r\n\x1a\n", f"{name}: invalid PNG"
        assert struct.unpack(">II", image[16:24]) == size, f"{name}: wrong dimensions"
        assert len(image) < 1_000_000, f"{name}: exceeds GitHub social image limit"
    sitemap = ElementTree.parse(site / "sitemap.xml")
    locations = {node.text for node in sitemap.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
    assert canonical_urls <= locations, "Published pages missing from sitemap"
    search = json.loads((site / "search/search_index.json").read_text(encoding="utf-8"))
    assert len(search["docs"]) >= len(pages), "Search index does not cover the published pages"
    assert not any("/work-in-progress/" in url or "/plans/" in url for url in locations)
    print(f"Validated {len(pages)} pages: canonical, descriptions, social metadata, sitemap, search")


if __name__ == "__main__":
    main()

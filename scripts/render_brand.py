"""Render project branding from one mark and brand.json; optionally export PNGs."""

import argparse
import json
import re
from hashlib import sha256
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs/assets"
FONT = "Segoe UI, Helvetica, Arial, sans-serif"


def digest(path):
    content = path.read_bytes() if path.suffix == ".png" else path.read_text(encoding="utf-8").encode("utf-8")
    return sha256(content).hexdigest()


def render():
    brand = json.loads((ASSETS / "brand.json").read_text(encoding="utf-8"))
    logo = (ASSETS / "logo.svg").read_text(encoding="utf-8")
    # Nested SVG preserves the original viewBox, including the 256px plugin mark.
    mark = re.sub(r'\s(?:width|height)="[^"]*"', "", logo, count=2) if "width=" in logo.split(">", 1)[0] else logo
    mark = mark.replace("<svg ", '<svg x="0" y="0" width="64" height="64" ', 1)
    name, tagline, color = (escape(brand[k], quote=True) for k in ("name", "tagline", "color"))
    result = {}
    for theme, bg, ink, muted, edge in (
        ("dark", "#0D1117", "#F0F6FC", "#B6C2CF", "#30363D"),
        ("light", "#FFFFFF", "#1F2328", "#59636E", "#D1D9E0"),
    ):
        banner = f'''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="96" viewBox="0 0 720 96" role="img" aria-label="{name}: {tagline}">
<title>{name}: {tagline}</title>
<rect x="1" y="1" width="718" height="94" rx="14" fill="{bg}" stroke="{edge}"/>
<g transform="translate(16 16)">{mark}</g>
<text x="98" y="43" font-family="{FONT}" font-size="29" font-weight="700" fill="{ink}">{name}</text>
<text x="98" y="69" font-family="{FONT}" font-size="15" fill="{muted}">{tagline}</text>
<path d="M613 48h72" stroke="{edge}" stroke-width="2"/>
<g fill="{bg}" stroke="{color}" stroke-width="2"><circle cx="613" cy="48" r="11"/><circle cx="649" cy="48" r="11"/><circle cx="685" cy="48" r="11"/></g>
<path d="m608 48 4 4 7-8" fill="none" stroke="{color}" stroke-width="2"/>
</svg>
'''
        if theme == "dark":
            result["docs/assets/banner.svg"] = banner
        if brand["sticky"]:
            result[f"docs/assets/sticky-banner-{theme}.svg"] = banner
    result[
        "docs/assets/social-preview.svg"
    ] = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640" role="img" aria-label="{name}: {tagline}">
<title>{name}: {tagline}</title>
<rect width="1280" height="640" fill="#0D1117"/>
<rect x="40" y="40" width="1200" height="560" rx="28" fill="#111827" stroke="#30363D" stroke-width="2"/>
<g transform="translate(90 100) scale(2.5)">{mark}</g>
<text x="90" y="370" font-family="{FONT}" font-size="76" font-weight="700" fill="{color}">{name}</text>
<text x="90" y="435" font-family="{FONT}" font-size="30" fill="#E5E7EB">{tagline}</text>
<text x="90" y="548" font-family="{FONT}" font-size="23" fill="#9CA3AF">github.com/gigaverse-app/{brand["repository"]}</text>
</svg>
'''
    for alias in brand["logo_aliases"]:
        result[alias] = logo
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true", help="Export PNGs with resvg-py 0.2.6")
    parser.add_argument("--check", action="store_true", help="Check generated SVGs without writing")
    args = parser.parse_args()
    stale = []
    for name, content in render().items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        parser.exit(1, "Regenerate branding: " + ", ".join(stale) + "\n")
    if args.png:
        import resvg_py

        for source, target, width, height in (
            ("logo.svg", "icon.png", 256, 256),
            ("banner.svg", "banner.png", 1440, 192),
            ("social-preview.svg", "social-preview.png", 1280, 640),
        ):
            (ASSETS / target).write_bytes(
                bytes(resvg_py.svg_to_bytes(svg_path=str(ASSETS / source), width=width, height=height))
            )
        names = [
            "logo.svg",
            "brand.json",
            "banner.svg",
            "social-preview.svg",
            "icon.png",
            "banner.png",
            "social-preview.png",
        ]
        hashes = {name: digest(ASSETS / name) for name in names}
        (ASSETS / "raster-sources.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    if args.check:
        hashes = json.loads((ASSETS / "raster-sources.json").read_text(encoding="utf-8"))
        for name, expected in hashes.items():
            if digest(ASSETS / name) != expected:
                parser.exit(1, "Regenerate PNG exports with --png: " + name + "\n")
    print("Brand assets verified" if args.check else "Brand assets rendered")


if __name__ == "__main__":
    main()

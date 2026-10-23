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


def motif() -> str:
    return """<g stroke-linecap="round" stroke-linejoin="round">
<rect x="2" y="10" width="27" height="34" rx="5" fill="#311B28" stroke="#713145" transform="rotate(-8 15 27)"/>
<path d="m10 22 9 9m0-9-9 9" stroke="#FB7185" stroke-width="2"/>
<path d="M33 27h15m-5-5 5 5-5 5" fill="none" stroke="#966172" stroke-width="2"/>
<rect x="54" y="10" width="27" height="34" rx="5" fill="#4C1D31" stroke="#FB7185"/>
<path d="M62 23c6-6 13-1 10 6m-4-1 4 2 3-4" fill="none" stroke="#FECDD3" stroke-width="2"/>
<path d="M85 27h15m-5-5 5 5-5 5" fill="none" stroke="#966172" stroke-width="2"/>
<rect x="106" y="10" width="27" height="34" rx="5" fill="#E11D48"/>
<path d="m113 27 5 5 9-11" fill="none" stroke="#FFFFFF" stroke-width="3"/>
<path d="M113 49c-28 15-61 13-86 1m5-3-6 3 3 5" fill="none" stroke="#FB7185" stroke-width="1.5" opacity="0.55"/>
</g>"""


def render():
    brand = json.loads((ASSETS / "brand.json").read_text(encoding="utf-8"))
    logo = (ASSETS / "logo.svg").read_text(encoding="utf-8")
    name, tagline, color, accent = (escape(brand[k], quote=True) for k in ("name", "tagline", "color", "accent"))
    first, second = (escape(line) for line in brand["banner_lines"])
    repository = brand["repository"]
    width, text_x, motif_x = (640, 152, 490)
    wordmark = f'D<tspan fill="{color}">ue</tspan>'
    defs = f"""<defs><linearGradient id="brand-glow" x1="0" y1="0" x2="1" y2="0">
<stop stop-color="{color}" stop-opacity="0.21"/><stop offset="0.55" stop-color="{color}" stop-opacity="0"/>
</linearGradient><radialGradient id="brand-aura" cx="0.1" cy="0.2" r="0.85">
<stop stop-color="{color}" stop-opacity="0.23"/><stop offset="0.75" stop-color="{color}" stop-opacity="0"/>
</radialGradient></defs>"""
    banner = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="64" viewBox="0 0 {width} 64" role="img" aria-label="{name}: {tagline}">
<title>{name}: {tagline}</title>
{defs}
<rect x="0.5" y="0.5" width="{width - 1}" height="63" rx="10" fill="#0D1117" stroke="#30363D"/>
<rect x="0.5" y="0.5" width="{width - 1}" height="63" rx="10" fill="url(#brand-glow)"/>
<g transform="translate(10 12) scale(0.625)">{logo}</g>
<text x="62" y="41" font-family="{FONT}" font-size="27" font-weight="800" letter-spacing="-0.5" fill="#F0F6FC">{wordmark}</text>
<text x="{text_x}" y="27" font-family="{FONT}" font-size="12" font-weight="600" fill="#C9D1D9">{first}</text>
<text x="{text_x}" y="45" font-family="{FONT}" font-size="12" fill="#9CA8B7">{second}</text>
<g transform="translate({motif_x} 1)">{motif()}</g>
</svg>
'''
    social = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640" role="img" aria-label="{name}: {tagline}">
<title>{name}: {tagline}</title>
{defs}
<rect width="1280" height="640" fill="#0D1117"/>
<rect width="1280" height="640" fill="url(#brand-aura)"/>
<g transform="translate(80 96) scale(2.5)">{logo}</g>
<text x="290" y="200" font-family="{FONT}" font-size="104" font-weight="800" letter-spacing="-2" fill="#F0F6FC">{wordmark}</text>
<text x="290" y="256" font-family="{FONT}" font-size="25" fill="#A9B5C4">Durable work for Django + PostgreSQL</text>
<text x="80" y="420" font-family="{FONT}" font-size="34" font-weight="600" fill="#F0F6FC">{first}</text>
<text x="80" y="470" font-family="{FONT}" font-size="34" font-weight="600" fill="{accent}">{second}</text>
<g transform="translate(730 335) scale(3.25)">{motif()}</g>
<text x="80" y="600" font-family="{FONT}" font-size="22" fill="#8B949E">github.com/gigaverse-app/{repository}</text>
</svg>
'''
    return {"docs/assets/banner.svg": banner, "docs/assets/social-preview.svg": social}


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
        # Optional export tool, installed by the documented uv --with command.
        import resvg_py  # pyrefly: ignore [missing-import]

        for source, target, width, height in (
            ("logo.svg", "icon.png", 256, 256),
            ("banner.svg", "banner.png", 1280, 128),
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
    # README images need the same cache refresh as documentation image URLs.
    brand = json.loads((ASSETS / "brand.json").read_text(encoding="utf-8"))
    image_url = f"https://raw.githubusercontent.com/gigaverse-app/{brand['repository']}/main/docs/assets/banner.png"
    versioned_url = image_url + "?v=" + digest(ASSETS / "banner.png")[:12]
    for readme in (ROOT / "README.md", ROOT / "package/README.md"):
        if not readme.exists():
            continue
        text = readme.read_text(encoding="utf-8")
        if args.png:
            readme.write_text(
                re.sub(re.escape(image_url) + r"(?:\?v=[0-9a-f]+)?", versioned_url, text),
                encoding="utf-8",
                newline="\n",
            )
        elif args.check and versioned_url not in text:
            parser.exit(1, "Refresh the README banner with --png: " + str(readme) + "\n")
    print("Brand assets verified" if args.check else "Brand assets rendered")


if __name__ == "__main__":
    main()

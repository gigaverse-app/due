"""Validate tracked Markdown's inline local file links (not remote URLs or anchors)."""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def missing_links(document: Path) -> list[str]:
    text = document.read_text(encoding="utf-8")
    # Code examples describe consumer repositories, not paths in this repository.
    text = re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*$", "", text)
    errors = []
    for target in re.findall(r"\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
        if target.startswith("{"):
            # Upstream issue/PR templates contain substitution fields such as {permalink}.
            continue
        url = urlsplit(target.strip("<>"))
        if url.scheme or url.netloc or not url.path:
            continue
        if not (document.parent / unquote(url.path)).exists():
            errors.append(f"{document}: missing {target}")
    return errors


def main() -> int:
    tracked = subprocess.check_output(["git", "ls-files", "-z", "*.md"], cwd=ROOT, text=True).split("\0")
    documents = [ROOT / path for path in tracked if path]
    errors = [error for document in documents for error in missing_links(document)]
    print("\n".join(errors) if errors else f"Checked local file links in {len(documents)} Markdown files")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())

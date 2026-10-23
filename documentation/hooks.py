"""Publish curated repository Markdown without maintaining a second copy."""

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from mkdocs.structure.files import File

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URIS: dict[str, str] = {}


def on_files(files, config):
    SOURCE_URIS.clear()
    for file in files:
        if file.abs_src_path and Path(file.abs_src_path).is_relative_to(ROOT):
            SOURCE_URIS[Path(file.abs_src_path).relative_to(ROOT).as_posix()] = file.src_uri
    for source in config.extra["published_sources"]:
        path = ROOT / source
        if not path.is_file():
            raise ValueError(f"Missing published source: {source}")
        files.append(File.generated(config, source, abs_src_path=str(path)))
        SOURCE_URIS[source] = source
    return files


def on_page_markdown(markdown, page, config, files):
    source = Path(page.file.abs_src_path).relative_to(ROOT).as_posix()
    repo = config.repo_url.rstrip("/")
    page.edit_url = f"{repo}/edit/main/{source}"
    if not page.meta.get("description"):
        for paragraph in markdown.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph and paragraph[0].isalpha():
                summary = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", paragraph)
                summary = re.sub(r"[`*_]", "", summary)
                page.meta["description"] = " ".join(summary.split())[:180]
                break

    def rewrite(target):
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path or target.startswith("{"):
            return target
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(url.path)))
        path = ROOT / resolved
        if not path.exists() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"{source}: broken local link {target}")
        suffix = ("?" + url.query if url.query else "") + ("#" + url.fragment if url.fragment else "")
        if resolved in SOURCE_URIS:
            return posixpath.relpath(SOURCE_URIS[resolved], posixpath.dirname(page.file.src_uri) or ".") + suffix
        # Source, downloads and internal audit records stay at their existing repository URL.
        kind = "tree" if path.is_dir() else "blob"
        return f"{repo}/{kind}/main/{resolved}{suffix}"

    # Preserve executable examples verbatim. Rewrite inline and reference Markdown links.
    parts = re.split(r"(^```[^\n]*\n.*?^```[^\n]*$)", markdown, flags=re.M | re.S)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r"(\]\()([^\s)]+)([^)]*\))", lambda m: m[1] + rewrite(m[2]) + m[3], parts[index])
        parts[index] = re.sub(r"^(\[[^]]+\]:\s*)(\S+)", lambda m: m[1] + rewrite(m[2]), parts[index], flags=re.M)
    return "".join(parts)

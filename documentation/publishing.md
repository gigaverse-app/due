# Build and publish these docs

The repository Markdown is the source. `mkdocs.yml` explicitly lists published
guides; `documentation/hooks.py` adds them to the build without copying prose.
Internal plans and audits are not added to the site. Links to unpublished source
files remain GitHub links. Broken local links fail the build.

```bash
uv venv .venv-docs --python 3.13
uv pip install --python .venv-docs -r documentation/requirements.txt
uv run --no-project --python .venv-docs mkdocs build --strict
uv run --no-project --python .venv-docs python documentation/check_site.py
uv run --no-project --python .venv-docs mkdocs serve
```

The **Documentation** workflow builds every PR and main revision with the same
pinned documentation dependencies as Read the Docs. Review its site artifact
before merging. Documentation changes do not alter runtime selection policy.

## Read the Docs setup

Import `https://github.com/gigaverse-app/due` into Read the Docs Community,
using project slug `due`, English, `main`, and `.readthedocs.yaml`.
Connect the GitHub integration so pushes rebuild automatically, and enable
pull request previews. Keep `latest` as the default until a published release
contains this configuration; then activate that release and choose `stable`.
Older tags without the configuration cannot build this site.

Verify a successful build, the homepage, search, a guide, a cross-project link,
`sitemap.xml`, and the page's canonical URL after publishing. The canonical base
comes from `READTHEDOCS_CANONICAL_URL`; local builds fall back to the public site.
Material generates page titles, descriptions, canonical links and a sitemap.
The override adds Open Graph and social summary metadata without analytics.

## Search measurement

Register the public URL prefix in Google Search Console using the owner's
account and submit the sitemap. If verification requires an HTML file or meta
tag, add the supplied verification value in a separate reviewed change.
Record indexing, impressions and clicks to tutorials before claiming SEO gains.
No search ranking or traffic improvement has been measured by this publishing change.

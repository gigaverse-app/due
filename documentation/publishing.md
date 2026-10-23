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

The maintainer's Google Search Console URL-prefix property is
`https://due.readthedocs.io/en/latest/`. Its public ownership verification tag
is in `documentation/overrides/main.html`; keep it present after verification.
This tag is intended to be public and does not authenticate application requests.

After deploying the tag, verify ownership in Search Console and submit
`https://due.readthedocs.io/en/latest/sitemap.xml`. Inspect indexing,
impressions, clicks, and tutorial landing pages over time. Property verification
and sitemap submission do not guarantee that Google indexes or ranks a page.
No search ranking or traffic improvement has been measured by this publishing change.
If the canonical domain changes, verify the new property and submit its sitemap.

## Keep the site useful

Publish instructions, API or safety boundaries, and comparisons that help a
reader make a decision. Use descriptive navigation labels and group pages by
the reader's task. Getting started comes before advanced reference; contributor
maintenance belongs in its own section. Avoid filename-derived titles and
expanded lists of every library or report.

Do not publish completed implementation checklists, extraction or migration
status ledgers, PR-by-PR progress, or generated run reports as guides. Extract
any lasting instruction into its existing guide and delete superseded notes.
Benchmark case studies may retain methods, immutable source identities and
limitations; link the raw evidence instead of giving each artifact a sidebar
entry. Keep the explicit `published_sources` list curated, and check incoming
links when removing a page.

## Maintain the visual examples

`documentation/assets/` contains the site's SVG identity and teaching images.
Keep logos vector-based and provide meaningful alternative text for every image.
Diagrams describe behavior; charts must link the source case study, name units,
start quantitative bars at zero and preserve measurement limitations.

Refresh product screenshots when the demonstrated interface changes. Run the
linked example or reporter, capture its real output, and keep the caption clear
about local rendering and simulated data. Do not manufacture passing results or
turn a sample screenshot into a current coverage/performance claim. Keep raw run
logs and JSON outside the source tree; PR validation records own that evidence.

# Editing Due in an application checkout

Source, reusable tests and library documentation share one versioned package. An adopter can change all three alongside its own application in one PR. Docs describing API contracts, invariants, failure/recovery guarantees, adoption, operations and migrations belong here. Documentation describing a particular demo or the public repository's releases belongs in the full OSS checkout.

```text
OSS main                              BE checkout
+ package/                            + libraries/due/
| + pyproject.toml, README, LICENSE    | + pyproject.toml, README, LICENSE
| + src/, tests/, docs/  ------------> | + src/, tests/, docs/
|                        git subtree  |   editable uv dependency
+ examples/, integration_tests/       + application code and integration docs
+ demos/ (where present)
+ CI, uv.lock, contributor/release docs
```

The generated `package` branch is the standard Git subtree split of `main:package`. It has the same tree as that directory, with paths relative to the package. No application files are filtered out after import, and no alternate package implementation exists. The full upstream checkout still showcases and tests the library against real applications.

## Run the reusable controls locally

```bash
# Run from this package directory (package/ upstream, libraries/due/ in BE).
export DUE_DATABASE_URL=postgresql://due:due@localhost:5432/due
export DUE_BROKER_URL=redis://localhost:6379/0
uv run --extra selftest python -m pytest --due-work-check-fixtures
```

The database role must be able to create test databases. These controls use a small real work-table fixture and dedicated Celery app; they do not boot the public demos. The `testing` extra remains the adopter harness dependency; `selftest` adds this library's own test tools.


Run these commands from the package directory so the package's pytest config and `tests` modules are selected, rather than the adopting application's modules. A package-only checkout resolves its self-test environment; full OSS CI uses the root lockfile. BE retains its own application lock and editable source declarations. Do not commit a second application lockfile here.

## Formatting follows the package

`pyproject.toml` in this directory owns the Ruff rules, line length and supported Python syntax. The full OSS workspace extends this configuration and adds only its own source paths and demo exclusions. An adopting repository's formatter discovers the same package configuration automatically, so editing the subtree cannot silently upgrade its syntax to the adopter's newer Python version. Run `ruff check .` and `ruff format --check .` here using the formatter installed by your development environment.

## Sync without a second source of truth

The public publisher runs only after successful CI on the exact `main` revision, and uses an ordinary fast-forward push:

```bash
# In the full OSS checkout, with complete history:
git fetch origin main
source_sha=$(git rev-parse origin/main)
package_sha=$(git subtree split --prefix=package "$source_sha")
test "$(git rev-parse "$source_sha:package")" = "$(git rev-parse "$package_sha^{tree}")"
git push origin "$package_sha:refs/heads/package"
```

`main` is the reviewed source; never edit the generated branch directly. Never force-push it, change the split options, or publish a PR head there. Before merge, a consumer can reconstruct the same split from the full PR revision and verify the tree equality above.

```text
BE PR: library code + docs + application binding
   | export only changes inside libraries/due/
   v
OSS PR: apply those changes under package/ on current main
   | run reusable controls + complete application/demo CI
   v
OSS main -- successful CI --> generated package branch
   | import reviewed package revision and record source/split/tree
   v
BE PR: updated subtree + application integration, then BE CI
```

A raw `git subtree split --prefix=libraries/due` in BE creates a *package-root* branch. Do not open that branch directly against OSS `main`: `main` also owns the demos and has a `package/` prefix. Export the delta from the recorded imported package revision, apply it in a full OSS checkout with `git apply --index --directory=package`, then open the OSS PR. Import changes after review, resolving overlap intentionally.

BE's canonical commands and recorded revision manifest live in its [public Due cutover guide](https://github.com/gigaverse-app/gigaverse-backend/blob/main/docs/technical-design/public-due-cutover.md). The importer verifies the full OSS revision's package tree against the split revision. An unchanged import is byte-for-byte identical; local edits are explicit pending upstream changes, not a permanent fork.

Paired CI overlays both committed BE package candidates into the corresponding full pinned OSS checkouts and resolves them together before running the library and application suites. Testing an untouched upstream checkout would not validate a proposed BE edit. Root examples and integration docs can be edited in the public PR whenever a library change needs them; they need not be copied into BE.

# Working on Due

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and validation.
Use [RELEASING.md](RELEASING.md) for the release process.

## Commits and pull requests

- Use Conventional Commits for commits you create and PR titles:
  `type(optional-scope): description`, for example
  `fix(parser): handle empty input`.
- Allowed types: `feat`, `fix`, `perf`, `revert`, `docs`, `style`,
  `refactor`, `test`, `build`, `ci`, and `chore`. Choose the type for the
  actual change. Mark breaking changes with `!` before the colon and explain
  the migration in the PR body.
- Repair an invalid PR title in place. Preserve useful work; do not close or
  replace the PR, or rewrite existing commits just to satisfy the title check.
- Squash merges use the checked PR title. Keep any `BREAKING CHANGE:` details
  in the body. Follow [CONTRIBUTING.md](CONTRIBUTING.md) and
  [RELEASING.md](RELEASING.md).

## Shared agent instructions

Edit this root `AGENTS.md` as the single source for Codex and Claude.
`CLAUDE.md` is an AgentSync-managed relative symlink to this file, not a
separate instruction document. The configuration is
[agentsync.toml](agentsync.toml). To restore and verify the link:

```bash
npx --yes @dallay/agentsync@1.52.3 apply
npx --yes @dallay/agentsync@1.52.3 status
```

Keep the symlink tracked in Git. See CONTRIBUTING.md for platform setup.

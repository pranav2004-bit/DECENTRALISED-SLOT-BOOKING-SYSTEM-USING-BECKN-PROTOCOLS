# Contributing

## Branching

Trunk-based. Branch off `main`, name branches `feat/…`, `fix/…`, `chore/…`, `docs/…`. Open a PR against `main`; merge only after CI is green. Squash-merge by default. See [ARCHITECTURE.md](ARCHITECTURE.md) for the rationale.

## Commit Messages

Conventional-commit style prefix, imperative mood, present tense:

```
feat(registry): add subscribe endpoint request validation
fix(bap): correct signing header casing
chore(ci): add container image scan stage
docs(architecture): record monorepo decision
```

Body (optional) explains *why*, not *what* — the diff already shows what changed.

## Pull Requests

Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md). A PR must:
- Pass the full CI pipeline (lint, unit tests, SCA, SAST, container scan)
- Reference the relevant [livetracker1.md](livetracker1.md) phase/task it advances
- Not touch `project_details.md` or the original client-provided content in any `*_details_v1.1.md` file — those are frozen; extend only via additive "Implementation note" callouts, per the convention already established in those files

## Code Style

- Python (registry, beckn-gateway, BAP/backend, BPP/backend): `ruff` + `black`, config in each app's `pyproject.toml`.
- TypeScript (BAP/web, BPP/web): `eslint` + `prettier`, config in each app's `package.json`/`.eslintrc`.
- Pre-commit hooks enforce both automatically — see [.pre-commit-config.yaml](.pre-commit-config.yaml).

## Secrets

Never commit real secrets, keys, or `.env` files — only `.env.example` templates with placeholder values. The pre-commit secrets-scanning hook blocks this automatically; see [SECURITY.md](SECURITY.md).

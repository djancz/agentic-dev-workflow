# Stack detection and default checks

Prefer the project's own entry points (make/just targets, package scripts) over these defaults. Every
command runs from the repository root and must exit non-zero on failure.

## Detection

| Manifest | Stack |
|---|---|
| `pyproject.toml`, `setup.py`, `requirements*.txt` | Python. Use the `uv run` prefix with `uv.lock`, `poetry run` with `poetry.lock` |
| `Cargo.toml` | Rust |
| `go.mod` | Go |
| `package.json` (+ `tsconfig.json`) | TypeScript/JS. The lockfile decides the tool: `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn, `bun.lock*` → bun, otherwise npm |
| anything else | ask; write the commands the human names |

## Python

| Check | Default | Install hint |
|---|---|---|
| format | `ruff format --check .` | `uv tool install ruff` / `pipx install ruff` |
| lint | `ruff check .` | (ruff) |
| typecheck | `mypy .` (or `pyright`, if configured) | `uv tool install mypy` |
| test-unit | `pytest -q` (limit to `tests/unit` if the split exists) | project dev dependency |
| test-integration | `pytest -q tests/integration` or `pytest -q -m integration` | — |
| build | `python -m build --outdir "$(mktemp -d)"` for packages; otherwise `N/A` | `uv tool install build` |
| deps-audit | `pip-audit` (`pip-audit -r requirements.txt` without a lock) | `uv tool install pip-audit` |
| sast | `bandit -q -r <package dir>` or `ruff check --select S .` | `uv tool install bandit` |

## Rust

| Check | Default | Install hint |
|---|---|---|
| format | `cargo fmt --all -- --check` | `rustup component add rustfmt` |
| lint | `cargo clippy --all-targets --all-features -- -D warnings` | `rustup component add clippy` |
| typecheck | `N/A — the compiler checks types (covered by build/test)` | — |
| test-unit | `cargo test --lib` (library crates), `cargo test --bins` (binary-only crates) | — |
| test-integration | `cargo test --test '*'` if `tests/` exists, else `N/A` | — |
| build | `cargo build --locked` | — |
| deps-audit | `cargo audit` (or `cargo deny check advisories`) | `cargo install cargo-audit --locked` |
| sast | `semgrep scan --config auto --error --quiet` | `pipx install semgrep` |

## Go

| Check | Default | Install hint |
|---|---|---|
| format | `test -z "$(gofmt -l .)"` | — |
| lint | `go vet ./...` and `staticcheck ./...` (or `golangci-lint run`) | `go install honnef.co/go/tools/cmd/staticcheck@latest` |
| typecheck | `N/A — go vet/build cover it` | — |
| test-unit | `go test -race -short ./...` | — |
| test-integration | `go test -race -tags=integration ./...` | — |
| build | `go build ./...` | — |
| deps-audit | `govulncheck ./...` | `go install golang.org/x/vuln/cmd/govulncheck@latest` |
| sast | `gosec -quiet ./...` | `go install github.com/securego/gosec/v2/cmd/gosec@latest` |

## TypeScript / JavaScript

Shown for npm. Replace `npx` and `npm run` with `pnpm exec` / `pnpm`, `yarn`, or `bunx` / `bun run` as
the lockfile dictates.

| Check | Default | Install hint |
|---|---|---|
| format | `npx prettier --check .` (or the project's biome/eslint format) | dev dependency |
| lint | `npx eslint .` | dev dependency |
| typecheck | `npx tsc --noEmit` | dev dependency |
| test-unit | `npm test` (or `npx vitest run`) | dev dependency |
| test-integration | the project's integration script, else `N/A` | — |
| build | `npm run build` | — |
| deps-audit | `npm audit --audit-level=high` | — |
| sast | `semgrep scan --config auto --error --quiet` | `pipx install semgrep` |

## Any stack

| Check | Default | Install hint |
|---|---|---|
| secrets | `gitleaks git --no-banner --redact` (scans the history, including checkpoint commits) | release binary, distro package, or `go install github.com/zricethezav/gitleaks/v8@latest` |
| deps-audit, fallback | `osv-scanner scan source -r .` (lockfiles of every ecosystem) | release binary or `go install github.com/google/osv-scanner/v2/cmd/osv-scanner@latest` |
| sast, fallback | `semgrep scan --config auto --error --quiet` | `pipx install semgrep` |

When a repository has several stacks, join a check's commands with `&&` in one cell, or give each stack
its own row (`test-unit (web)`, `test-unit (api)`).

## Web apps: dev server and e2e

Find the dev server in `package.json` scripts (`dev`, `start`, `preview`), `manage.py runserver`,
`uvicorn`, `flask run` or `rails server`. Find e2e tests from `playwright.config.*`, `cypress.config.*`,
`@playwright/test` or `cypress` in `package.json`, `pytest-playwright`, or a `tests/e2e/` directory.

- **Dev server line.** The command runs in the foreground, binds to `127.0.0.1` on a fixed port that
  fails when it is taken, and has the reloader off where the framework has one. Examples:
  `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort` at `http://127.0.0.1:5173/`, or
  `uv run python manage.py runserver 127.0.0.1:8000 --noreload` at `http://127.0.0.1:8000/`.
- **test-e2e** is one command that starts the server, runs the tests and stops the server, also when
  they fail. It never tests against a server it did not start.

| Setup | Default `test-e2e` |
|---|---|
| Playwright (JS/TS) with `webServer` in its config | `CI=1 npx playwright test` — `CI=1` turns off the usual `reuseExistingServer: !process.env.CI`, so a server already on the port fails the run instead of being tested |
| Cypress, pytest-playwright, HTTP tests, or Playwright without `webServer` | `python3 <skills-dir>/wf-init/scripts/dev-server.py run --url <url> --server '<dev server command>' -- <test command>`, e.g. `-- npx cypress run` or `-- uv run pytest -q tests/e2e` |
| Go, Rust | a server inside the test process (`httptest.NewServer`, a listener on port 0) needs no helper; such tests belong in `test-integration` |
| no e2e tests | `N/A — no e2e tests`; for a web app, suggest setting them up as a `wf task` |

If a Playwright config sets `reuseExistingServer: true`, tell the human: the check could test an old
server. Playwright's `test-results/` and `playwright-report/` and Cypress's `cypress/screenshots/` and
`cypress/videos/` must be gitignored. Browsers are downloaded once (`npx playwright install`); until then
the check is `NOT RUN (tool missing)`.

# Finding and applying updates

Prefer the project's own scripts (`make outdated`, a `package.json` script) over these defaults. Every
command runs from the repository root. The lookups need the network; without it they are
`NOT RUN (no network)`.

"Latest in range" is the newest version the manifest's constraint already allows; "latest" is the newest
release. An update is **routine** when it stays within the current major (for `0.x`, within the current
minor, where a minor may break). Anything else is a major upgrade and a proposed task.

## Detection

| Manifest or lockfile | Ecosystem |
|---|---|
| `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock*` | npm, pnpm, yarn, bun (the lockfile decides) |
| `uv.lock`, `poetry.lock`, `requirements*.txt` | uv, poetry, pip |
| `Cargo.lock` | cargo |
| `go.sum` | go |
| `.github/workflows/`, `.gitea/workflows/`, `.forgejo/workflows/` | CI actions |
| `Dockerfile*`, `compose*.y*ml` | container base images |

## Dependencies

| Ecosystem | Outdated | Routine update | Pin a newer minor |
|---|---|---|---|
| npm | `npm outdated --json` (exit 1 means "something is outdated", not a failure) | `npm update` | `npm install <pkg>@<version>` |
| pnpm | `pnpm outdated --format json` | `pnpm update` | `pnpm add <pkg>@<version>` |
| yarn 1 | `yarn outdated` | `yarn upgrade` | `yarn add <pkg>@<version>` |
| yarn 2+ | `npm view <pkg> version` per direct dependency | `yarn up <pkg>@<version>` | same |
| bun | `bun outdated` | `bun update` | `bun add <pkg>@<version>` |
| uv | `uv tree --outdated --depth 1` | `uv lock --upgrade`, then `uv sync` | edit the constraint in `pyproject.toml`, then `uv lock` |
| poetry | `poetry show --outdated --top-level` | `poetry update` | `poetry add <pkg>@^<version>` |
| pip | `pip list --outdated` in the project's virtualenv | edit the `==` pins, or `pip-compile --upgrade-package <pkg>` where `requirements.in` exists | same |
| cargo | `cargo update --dry-run --verbose` (lists what stays behind latest) | `cargo update` | edit `Cargo.toml`, then `cargo update -p <crate>` |
| go | `go list -m -u -f '{{if and (not .Indirect) .Update}}{{.Path}} {{.Version}} -> {{.Update.Version}}{{end}}' all` | `go get -u=patch ./...`, then `go mod tidy` | `go get <module>@<version>`, then `go mod tidy` |

- In Go, a new major version is a new module path (`/v2`): always a proposed task.
- A lockfile-only update is still a dependency change: `Sensitivity: high`, and `wf sec` reviews it.
- npm-family install scripts: `npm view <pkg>@<version> scripts` shows `preinstall`, `install` and
  `postinstall`. A new or changed one goes into the summary's *Security notes*.

## CI actions

List every `uses: <owner>/<repo>@<ref>` in the workflow directories. Find the newest release with
`git ls-remote --tags --refs https://github.com/<owner>/<repo>` (or the forge that hosts the action).

- `@v4` style: a newer `v4.x` is picked up automatically; a `v5` is a major.
- `@<sha>` pinned: keep pinning by SHA; update to the SHA of the newer tag and keep the tag in a comment.
- All CI action majors together are one concern, and so one proposed task.

## Runtimes and container images

Find the versions in use: `.nvmrc`, `.node-version`, `engines` in `package.json`, `.python-version`,
`requires-python`, `rust-toolchain.toml`, the `go` line of `go.mod`, and the tags in `FROM` lines and
compose `image:` lines.

Look up the end of life with `curl -fsS https://endoflife.date/api/<product>.json` (`nodejs`, `python`,
`go`, `postgresql`, `redis`, `debian`, `ubuntu`, `alpine`, …). A runtime or image past its end of life, or
within 90 days of it, is a proposed task. Report a base image without a pinned tag (`latest`, or none).

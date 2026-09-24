# Deploy runbook — <environment>

- Environment: production
- Host: <SSH host alias; users, keys and ports stay in the SSH configuration, never here>
- Kind: <compose | systemd>
- App dir: <absolute path on the host>
- Backup dir: <absolute path on the host, outside the app dir, with room for several backups>
- Release ref: origin/<base>
- Release checks: test-unit, build
- Current version: `<command that prints the deployed commit sha>`
- Migrations path: <path in the repository, or none>
- Timeout: 600
- Approved: —

Placeholders: `{sha}` the release, `{prev}` the deployed version, `{ts}` the timestamp of this deploy,
`{backup_dir}` the Backup dir. Commands run from this machine, usually as `ssh <host> '…'`. No passwords
in commands; see the recipes.

## Preflight (read-only)

| Check | Command | Expect |
|---|---|---|
| host reachable | `ssh <host> true` | exit 0 |
| free space | `ssh <host> df -h {backup_dir}` | <room for the backups> |
| service healthy | `…` | … |

## Backups

Every stateful component: databases, volumes or directories with user data, config with secrets.

| Component | Command | Verify |
|---|---|---|
| <database> | `…` | `…` |

## Deploy

| # | Step | Command | Changes data? |
|---|---|---|---|
| 1 | <step> | `…` | <no \| yes: what> |

## Verify

| Check | Command | Expect | Retries |
|---|---|---|---|
| version | `<Current version>` | `{sha}` | 1 |
| health | `curl -fsS <health url>` | exit 0 | 5 × 10 s |

## Rollback — code

| # | Step | Command |
|---|---|---|
| 1 | <step> | `…` with `{prev}` |

## Rollback — data (destructive: discards writes since the backup; separate yes)

| Component | Command |
|---|---|
| <database> | `…` |

## Notes

<Expected downtime, maintenance mode, components that are not backed up and why, who to call.>

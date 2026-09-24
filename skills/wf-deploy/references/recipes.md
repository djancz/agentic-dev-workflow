# Deploy recipes

Starting points for a runbook. Adapt names, paths and services to the project; do not copy a row whose
component the project does not have. `<host>` is the SSH alias, `<app>` the app directory on the host.
Every command runs from the local machine as `ssh <host> '…'`. In the tables, `\|` is a shell pipe; write
it as `|` in the runbook.

## Rules for every runbook

- **Immutable versions.** Deploy by commit sha: image tag `{sha}` or directory `releases/{sha}`. Never
  deploy `latest` or a moving branch. Then `{prev}` still exists when a rollback needs it.
- **Backups.** Create them with `umask 077` in `{backup_dir}`, outside `<app>`, named
  `<component>-{ts}.<ext>`. A copy on another host is better still. Add a Backups row that copies with
  `rsync` or `scp`, never into the project.
- **Credentials.** Never put a password in a command: it ends up in process lists, logs and the deploy
  log. Use the host's option files (`~/.pgpass`, `~/.my.cnf`, mode 600), peer authentication, or the
  container's own environment.
- **Secrets.** Back up config files with secrets as archives. Verify them by listing names
  (`tar -tzf`), never by printing content.
- **Migrations.** A migration that drops or renames a column makes a code rollback unsafe. Prefer
  expand/contract: add first, remove in a later release. Put a migration in its own Deploy row, marked
  `Changes data? yes`.
- **Downtime.** If a step restarts or stops a service, say so under *Notes*.

## Docker Compose over SSH

| Purpose | Command |
|---|---|
| Current version | `ssh <host> cat <app>/REVISION` |
| Preflight: services | `ssh <host> 'cd <app> && docker compose ps --format "{{.Service}} {{.State}} {{.Health}}"'` |
| Preflight: space | `ssh <host> df -h {backup_dir}` |
| Preflight: no local edits | `ssh <host> git -C <app> status --porcelain` (empty; checkout variant) |
| Backup: images in use | `ssh <host> 'cd <app> && docker compose images'` (record in the log) |
| Backup: volume | `ssh <host> 'docker run --rm -v <volume>:/data:ro -v {backup_dir}:/backup alpine sh -c "umask 077 && tar -czf /backup/<volume>-{ts}.tgz -C /data ."'` |
| Verify: volume | `ssh <host> 'docker run --rm -v {backup_dir}:/backup:ro alpine tar -tzf /backup/<volume>-{ts}.tgz \| head -n 5'` |
| Backup: config | `ssh <host> 'umask 077 && tar -czf {backup_dir}/config-{ts}.tgz -C <app> .env compose.override.yml'` |
| Deploy (image tag) | `ssh <host> 'cd <app> && sed -i "s/^APP_TAG=.*/APP_TAG={sha}/" .env && docker compose pull'` |
| Deploy (checkout) | `ssh <host> 'git -C <app> fetch origin && git -C <app> checkout --detach {sha} && cd <app> && docker compose build'` |
| Migrate | `ssh <host> 'cd <app> && docker compose run --rm <app-service> <migrate command>'` |
| Switch | `ssh <host> 'cd <app> && docker compose up -d --remove-orphans && echo {sha} > REVISION'` |
| Verify | `docker compose ps` shows every service running (and healthy where a healthcheck exists); `curl -fsS <health url>` |
| Rollback: code | the Deploy and Switch rows with `{prev}` instead of `{sha}` |

For a database inside Compose, prefix the commands in *Databases* with
`docker compose exec -T <db-service>`. `-T` disables the TTY; without it a binary dump is corrupted.
The files stay on the host, so redirect output and input there instead of passing paths, for example:
- backup: `ssh <host> 'umask 077 && cd <app> && docker compose exec -T db pg_dump -U <user> -Fc <db> > {backup_dir}/db-{ts}.dump'`
- verify: `ssh <host> 'cd <app> && docker compose exec -T db pg_restore --list < {backup_dir}/db-{ts}.dump | head -n 5'`

## systemd over SSH

Layout on the host: `<app>/releases/<sha>/`, `<app>/current` (a symlink to the live release) and
`<app>/shared/` (config and user files, linked into each release). Keep at least the previous release.

| Purpose | Command |
|---|---|
| Current version | `ssh <host> 'basename "$(readlink <app>/current)"'` |
| Preflight | `ssh <host> systemctl is-active <unit>`; `ssh <host> df -h {backup_dir} <app>` |
| Backup: user files | `ssh <host> 'umask 077 && tar -czf {backup_dir}/files-{ts}.tgz -C <app>/shared .'` |
| Upload | `git archive {sha} \| ssh <host> 'mkdir -p <app>/releases/{sha} && tar -x -C <app>/releases/{sha}'` |
| Build | the project's install or build command inside `<app>/releases/{sha}`, with pinned or hashed dependencies |
| Link shared | `ssh <host> 'ln -sfn <app>/shared/<item> <app>/releases/{sha}/<item>'` for each shared item |
| Migrate | the migrate command run from `<app>/releases/{sha}` |
| Switch | `ssh <host> 'ln -sfn <app>/releases/{sha} <app>/current.tmp && mv -T <app>/current.tmp <app>/current && sudo systemctl restart <unit>'` |
| Verify | `systemctl is-active <unit>`; `journalctl -u <unit> -n 40 --no-pager`; `curl -fsS <health url>` |
| Rollback: code | the Switch row with `{prev}` instead of `{sha}` |

`mv -T` replaces the symlink atomically; `ln -sfn` on the live link does not.

## Databases

`{f}` stands for `{backup_dir}/<component>-{ts}`.

| Database | Backup | Verify | Restore (destructive) |
|---|---|---|---|
| PostgreSQL | `pg_dump -Fc -U <user> <db> > {f}.dump` | `pg_restore --list {f}.dump \| head -n 5` | `pg_restore --clean --if-exists --single-transaction -U <user> -d <db> {f}.dump` |
| MySQL / MariaDB | `mysqldump --single-transaction --routines --triggers --events <db> \| gzip > {f}.sql.gz` | `gzip -t {f}.sql.gz && zcat {f}.sql.gz \| tail -n 1` shows `-- Dump completed` | `zcat {f}.sql.gz \| mysql <db>` |
| SQLite | `sqlite3 <db file> ".backup '{f}.sqlite'"` | `sqlite3 {f}.sqlite 'PRAGMA integrity_check'` prints `ok` | stop the service, copy the file back, start |
| Redis | `redis-cli BGSAVE`, wait until `redis-cli LASTSAVE` changes, copy `dump.rdb` to `{f}.rdb` | `redis-check-rdb {f}.rdb` | stop, replace `dump.rdb`, start |
| Files | `tar -czf {f}.tgz -C <dir> .` | `tar -tzf {f}.tgz \| head -n 5` | extract into an emptied directory |

Consistency while the app runs: `pg_dump` reads one snapshot. `mysqldump --single-transaction` is
consistent only for InnoDB tables. SQLite's `.backup` is safe, but copying the file is not. Files written
during the tar may be missed. Stop the app for the restore so it cannot write in between.

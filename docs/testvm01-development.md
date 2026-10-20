# PV2Hash development environment on testvm01

Updated: 2026-10-20. This is the permanent shared development/GUI review
instance. It is **not** a production hashpi. The old frontend previews and
legacy smoke-test processes were stopped on 2026-10-20; their Git worktrees
were deliberately retained for reference.

## URLs and services

| Purpose | Local URL | Service | Source |
|---|---|---|---|
| Complete PV2Hash test installation, including compiled React GUI | `http://10.20.0.51:8000/ui-next/` | `pv2hash-dev.service` | `/home/chatgpt/pv2hash-dev-current` |
| Live React/Vite preview with hot reload | `http://10.20.0.51:5173/ui-next/` | `pv2hash-vite.service` | `/home/chatgpt/pv2hash-dev-current/frontend` |

Both units are defined as individual systemd files in
`/etc/systemd/system/`. The previous nested
`pv2hash-dev.service.d/90-*.conf` and `95-*.conf` overrides were retired.
The services run as the unprivileged `chatgpt` user and are enabled on boot.

Vite proxies `/api`, `/static` and the config-export endpoint to the Core
on port **8000**. Consequently these are two **frontends of one backend**,
not separate independent test instances. Ports 5174–5178 and 18083–18084
are no longer in use by PV2Hash.

## Repository and data separation

- The source working tree is a dedicated Git worktree created from
  `origin/main` (initially at release `r2026.10.20.2`, Core 0.10.9,
  GUI 2.1.8). Local branch: `dev/testvm01-live`.
- Python virtual environment: `/srv/pv2hash/.venv`, shared by the backend.
- Frontend dependencies: `frontend/node_modules` in the current worktree.
- Bundled React GUI: `pv2hash/gui -> ../frontend/dist`. Rebuild it using
  `npm run build` from the frontend directory. Port 8000 serves this
  compiled bundle; port 5173 previews source changes via Vite.
- Persistent test configuration and SQLite databases:
  `data -> /srv/pv2hash/data`. **Never delete this path when replacing
  source code.** It holds config, identity, DataLogger history, forecast
  and exporter state. The original simulator configuration was retained,
  including its enabled InfluxDB exporter to the development bucket.
- The initial local build-identity file, `pv2hash/build_info.json`,
  describes the checked-out source release. Update it when a new release
  baseline is selected. It is generated locally, not committed.
- Local Git excludes protect the generated `data`, `pv2hash/gui`
  and `pv2hash/build_info.json` paths from accidental commits.

The controlled mining simulator is the only miner with automatic control
enabled in the preserved test configuration. An actual AxeOS miner is also
present for monitoring, with automatic control disabled. Verify these
settings before changing the dev configuration.

## Test GUI changes before a PR

1. Check `git status` and create a feature branch from the appropriate,
   current base. Do not discard uncommitted work in existing worktrees.
2. Edit the frontend in the workspace backing the Vite service; the user
   can immediately view the changes at **port 5173**.
3. The Core on port 8000 keeps running unchanged until the developer
   deliberately restarts it after backend edits. No production hashpi
   should be modified for a GUI preview.
4. Run the Python and frontend test suites. For release-level checking
   use a clean Git worktree without the generated local
   `pv2hash/build_info.json`: a few source-only tests expect the
   version fallback and will fail when the generated release identity is
   present in the same worktree. Do not remove the live service's
   identity file while it is running.
5. Review the GUI on 5173, create the PR, merge it only after approval,
   and use the regular GitHub Release workflow for deployment.
6. To refresh the complete GUI served on port 8000, build `frontend`
   and restart the Core when needed. A new production release is not
   required for testvm01 GUI review.

Keep this shared dev worktree synchronized by fetch/fast-forward of
`origin/main` when clean, and rebuild the compiled GUI. Never force-reset
a workspace that contains accepted or uncommitted changes. Synchronize
frontend dependencies with `npm ci` if the lockfile changed.

## Operational checks

```bash
systemctl is-active pv2hash-dev.service pv2hash-vite.service
curl -fsS http://127.0.0.1:8000/api/system/build-info
curl -fsS http://127.0.0.1:8000/api/runtime/snapshot
curl -fsS http://127.0.0.1:5173/api/system/build-info
journalctl -u pv2hash-dev.service -n 60 --no-pager
journalctl -u pv2hash-vite.service -n 40 --no-pager
```

Note that `/api/runtime/snapshot` may include device or location metadata;
do not publish an unfiltered snapshot. Logs and configuration may contain
internal addresses. Never commit secrets, database content, local credentials
or user-specific instance identifiers.

## Backup and rollback

Before replacing the service on 2026-10-20, the prior systemd unit,
its two overrides, the instance/configuration JSON and **consistent SQLite
online backups** were saved under:

`/home/chatgpt/pv2hash-testvm01-backups/20261020-0824/`

The old worktrees were not deleted. If a deployment fails, stop the new
service and restore the previous unit and overrides from this backup,
reload systemd, and restart the old service. Reverting the program code
normally does not require overwriting the currently active SQLite data;
review possible schema changes before any database rollback.

Other services on this shared VM (Docker, Lightning-Lab, TSV-Helper, etc.)
are explicitly outside the scope of PV2Hash cleanup.

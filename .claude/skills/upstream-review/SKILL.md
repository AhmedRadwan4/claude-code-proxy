---
name: upstream-review
description: Review new commits on the untrusted public upstream repo (devasheeshG/claude-code-proxy) before merging them into this fork. Use when the user says "check upstream", "sync with upstream", "pull from the original repo", "any new commits upstream?", or "/upstream-review".
---

# Upstream review

`upstream` (devasheeshG/claude-code-proxy) is **untrusted**. Its maintainer does not
review PRs, and its commits can be good, broken, or harmful. This fork (`origin`,
AhmedRadwan4/claude-code-proxy) is the source of truth. Nothing from upstream lands
on `main` until every new commit has been read and the user approves.

## Steps

1. Fetch and list what's new:
   ```
   git fetch upstream
   git log --oneline --reverse HEAD..upstream/main
   git log --oneline upstream/main..HEAD
   git diff --stat HEAD...upstream/main
   ```
   If the first list is empty: report "upstream has nothing new" and stop.

2. Read every commit's diff (`git show <sha>`), not only the stat. For each one, note
   what it does and whether it's useful to this fork.

3. Security pass over the full range (`git diff HEAD...upstream/main`). Flag any of:
   - New outbound URLs/hosts, telemetry, or webhooks.
   - `exec`, `eval`, `subprocess`, shell-outs, `curl | sh`, base64 blobs, minified code.
   - Code that reads, logs, or sends secrets: tokens, `FERNET_KEY`, `JWT_SECRET`,
     passwords, OAuth tokens, API keys.
   - Auth or permission changes: `security.py`, `require_admin`, CORS, the model
     allowlist, per-user limits.
   - Changes to `.github/workflows/` (new `secrets.*`, `permissions: write`,
     `pull_request_target`, third-party actions not pinned to a tag/SHA).
   - Dependency changes (`pyproject.toml`, `uv.lock`, `package.json`, `pnpm-lock.yaml`):
     list each added or bumped package.
   - Deployment changes (`docker-compose*.yml`, `scripts/blue-green.sh`, Traefik,
     `Dockerfile`), which need the CLAUDE.md deployment-safety checks.
   - Schema changes (`alembic/versions/001_initial_migration.py`,
     `app/scripts/migrate.py`, `postgres/schemas.py`): check that existing DBs still
     upgrade.

4. Check for conflicts with this fork's own commits:
   `git merge-tree --write-tree HEAD upstream/main` (a non-zero exit or conflict lines
   mean they conflict). Name the files.

5. Test upstream's code in a throwaway worktree. Never test it in the working tree:
   ```
   git worktree add ../ccp-upstream-check upstream/main
   ```
   - Backend: `cd backend && uv run pytest -q`. Needs Docker running (testcontainers).
     The 3 `test_notifications.py` timezone tests fail on Windows because Python has
     no IANA timezone data there. That's the machine, not the code.
   - Frontend: `cd frontend && pnpm install --frozen-lockfile && node --test tests/*.test.mjs && pnpm exec tsc --noEmit && pnpm lint`
   - Then: `git worktree remove --force ../ccp-upstream-check`

6. Report, then **stop and ask**. Give:
   - Verdict: merge all / merge some (list the SHAs) / skip.
   - One line per commit: SHA, what it does, keep or skip, any flag.
   - Security flags, conflicts, and test results with the actual counts.

7. Only after the user approves:
   - All commits, no local divergence: `git merge --ff-only upstream/main`
   - All commits, with divergence: `git merge --no-ff upstream/main`
   - Some commits: `git cherry-pick <sha>...` in order
   - Re-run the backend tests on the result. Push to `origin` or deploy only when
     the user asks.
   Never push to `upstream` or open PRs there.

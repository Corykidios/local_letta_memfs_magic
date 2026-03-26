---
name: letta-local-memfs
description: >
  Setup, diagnose, and extend git-backed memory (MemFS) on a self-hosted
  Letta OSS server. Use this skill whenever the user mentions Letta memfs,
  context repositories, git-backed memory, local MemFS, self-hosted Letta
  memory, or gets errors like "only available on Letta Cloud", "expected
  flush after ref listing", or "501 Not Implemented" during a Letta git
  clone. Also use it when the user wants to understand how Letta stores
  memory, set up the git-memfs-server sidecar, patch the Letta source,
  configure conf.yaml, or share this setup with the community.
---

# Letta Local MemFS Skill

This skill enables Claude to guide users through running Letta's git-backed
memory (MemFS / context repositories) on a fully self-hosted OSS Letta
server — no Letta Cloud account required.

## What This Is

Letta MemFS is officially cloud-only. But the OSS server already contains
all the internal plumbing (`GitEnabledBlockManager`, `LocalStorageBackend`,
`GitOperations`). The only missing piece is the git HTTP transport layer.

This skill deploys a ~100-line Python sidecar (`git-memfs-server.py`) that
provides that transport, backed by bare repos on the local filesystem, using
`git http-backend` (ships with any standard Git install).

**No dulwich. No Gitea. No cloud. Just Python stdlib + Git.**

## Architecture (memorize this)

```
Letta Code (letta.js)
    │ git clone/push/pull
    ▼
Letta Server :8283
    │ /v1/git/{agent_id}/state.git/* → httpx proxy
    ▼
git-memfs-server.py :8285
    │ GIT_PROJECT_ROOT + PATH_INFO → subprocess
    ▼
git http-backend
    │ reads/writes bare repo
    ▼
~/.letta/memfs/repository/{org_id}/{agent_id}/repo.git/
```

Redis :6379 is also required — `GitOperations.commit()` acquires a Redis
lock per agent. This is Letta's existing Redis, not something new.

## Required Components

| Component | What it does |
|-----------|-------------|
| `git-memfs-server.py` | The sidecar — serves git smart-HTTP on :8285 |
| `~/.letta/conf.yaml` | Tells Letta server where the sidecar is |
| `LETTA_MEMFS_LOCAL=1` | Tells Letta Code to skip the cloud-only check |
| One-line source patch | Fixes a kwarg mismatch in `memfs_client_base.py` |
| Redis | Already needed by Letta; required for commit locking |

---

## Workflow: Full Setup From Scratch

### 1. Install the sidecar server

Read `references/git-memfs-server.py` and write it to the user's machine.
Good location: anywhere stable, e.g. `~/letta-memfs/git-memfs-server.py`.

### 2. Patch the Letta source (one line)

In the Letta install, open:
`letta/services/memory_repo/memfs_client_base.py`

Find (around line 54):
```python
self.git = GitOperations(storage=self.storage, redis_client=None)
```
Change to:
```python
self.git = GitOperations(storage=self.storage)
```

`GitOperations.__init__` doesn't accept `redis_client` — mismatch between
base and cloud client. This is the only source change needed.

### 3. Configure conf.yaml

Create or edit `~/.letta/conf.yaml`:
```yaml
letta:
  memfs_service_url: "http://localhost:8285"
```

**Critical:** the nested `letta:` key is mandatory. Bare env var style
(`LETTA_MEMFS_SERVICE_URL: "..."`) does NOT work — Letta's config loader
only processes keys under known top-level sections (`letta:`, `model:`, etc).

### 4. Set the client bypass env var

In `~/.bashrc`:
```bash
export LETTA_MEMFS_LOCAL=1
```

And ensure `~/.bash_profile` sources it (Git Bash opens as login shell):
```bash
# ~/.bash_profile
if [ -f ~/.bashrc ]; then source ~/.bashrc; fi
cd ~
```

The `cd ~` prevents the EPERM error from running `letta` with CWD at `/`
(which maps to the Git install root on Windows).

### 5. Startup order

```
1. Redis          (redis-server --protected-mode no)
2. git-memfs-server.py  (python git-memfs-server.py)
3. Letta server   (uv run letta server  OR  letta server)
4. Letta Code     (letta --memfs)   ← Git Bash only on Windows
```

`--memfs` is only needed once per agent. After first enable, the
`git-memory-enabled` tag is stored on the agent server-side.

---

## Diagnosing Common Errors

See `references/troubleshooting.md` for the full table.
Quick reference for the most common ones:

**"only available on Letta Cloud"**
→ `LETTA_MEMFS_LOCAL` not in environment. Open fresh Git Bash, or
  check that `.bash_profile` sources `.bashrc`.

**"501 Not Implemented" during git clone**
→ Letta server started before `conf.yaml` existed, OR conf.yaml has
  wrong format (bare key instead of nested under `letta:`).
  Verify: `uv run python -c "from letta.settings import settings; print(settings.memfs_service_url)"`
  Should print the URL, not `None`.

**"expected flush after ref listing"**
→ git-memfs-server.py is NOT running, OR an old version without the
  chunked transfer encoding fix. Restart sidecar with latest version.

**"TypeError: GitOperations.__init__() got an unexpected keyword argument 'redis_client'"**
→ Source patch not applied. See Step 2 above.

**"EPERM: mkdir ...Git\.letta"**
→ Running `letta` from `/` in Git Bash. The `cd ~` in `.bash_profile`
  prevents this permanently.

**Letta server port 8283 already in use**
→ Old instance still running. Kill with: `python scripts/kill_8283.py`

---

## Verifying the Setup

Run these checks in order:

```bash
# 1. Confirm Letta server sees the sidecar URL
uv run python -c "from letta.settings import settings; print(settings.memfs_service_url)"
# Expected: http://localhost:8285

# 2. Confirm env var is live
echo $LETTA_MEMFS_LOCAL
# Expected: 1

# 3. Test full proxy chain (with both servers running)
python scripts/check_settings.py
# Expected: STATUS 200, Content-Type: application/x-git-upload-pack-advertisement
```

---

---

## Docker Deployment

For Docker Compose environments, the sidecar runs as a separate
container. Key differences from native:

| Aspect | Native | Docker |
|--------|--------|--------|
| Sidecar address | `localhost:8285` | `memfs-sidecar:8285` |
| Config method | `~/.letta/conf.yaml` | `LETTA_MEMFS_SERVICE_URL` env var |
| Source patch | Edit file in-place | Volume-mount override (`:ro`) |
| `LETTA_MEMFS_LOCAL=1` | Required | Not needed server-side |

### Docker-Specific Errors

**Block updates show "(postgres-only path)" for shared blocks:**
The `_get_agent_id_for_block` method returns a non-deterministic agent
for blocks shared across multiple agents. If that agent lacks the
`git-memory-enabled` tag, git commits are silently skipped. Fix with
the LEFT JOIN + CASE ORDER BY patch in `block_manager_git.py`. See
`docs/SHARED-BLOCK-BUG.md`.

**Sidecar healthy but no git commits happen:**
Verify both containers mount the same host directory:
- Sidecar: `~/.letta/.persist/memfs` → `/data/memfs/repository`
- Letta: `~/.letta/.persist/memfs` → `/root/.letta/memfs/repository`

**"MemfsClient initialized" missing from Letta startup:**
The `memfs_client_base.py` override isn't mounted. Check your volume
mount is pointing to the correct file path.

---

## Reference Files

- `references/architecture.md` — Deep technical internals, all bugs found
  during original setup, how to extend the sidecar
- `references/troubleshooting.md` — Full error table with causes and fixes
- `references/git-memfs-server.py` — The sidecar server source
- `docs/DOCKER-DEPLOYMENT.md` — Complete Docker Compose deployment guide
- `docs/SHARED-BLOCK-BUG.md` — Shared block git commit skip bug analysis

# Docker Deployment Guide

Complete guide for running Letta's git-backed memory (MemFS) in a Docker
Compose environment. Verified working on Letta v0.16.6 with the sidecar
container approach.

---

## Architecture

```
Letta Code (letta.js, Node)
    |
    | git clone/push/pull via HTTP
    v
Letta Container :8283
    |
    | /v1/git/{agent_id}/state.git/* --> httpx proxy
    v
memfs-sidecar Container :8285
    |
    | GIT_PROJECT_ROOT + PATH_INFO --> subprocess
    v
git http-backend
    |
    | reads/writes bare repo on shared volume
    v
bind mount: ~/.letta/.persist/memfs/
```

Both containers mount the same host directory:
- **memfs-sidecar** mounts it at `/data/memfs/repository`
- **letta** mounts it at `/root/.letta/memfs/repository`

The `LocalStorageBackend` inside Letta writes git objects directly to
this directory. The sidecar provides the HTTP transport layer so that
Letta Code (the desktop client) can clone/push/pull via the `/v1/git/`
proxy endpoint.

---

## Prerequisites

- Docker and Docker Compose v2+
- A running PostgreSQL instance (pgvector recommended)
- A running Redis instance
- The `local_letta_memfs_magic` repo checked out alongside your Letta stack

---

## Step 1 - Create the Storage Directory

```bash
mkdir -p ~/.letta/.persist/memfs
```

This directory will hold all the bare git repos, organized as:
```
~/.letta/.persist/memfs/{org_id}/{agent_id}/repo.git/
```

---

## Step 2 - Add the memfs-sidecar Service

Add to your `compose.yaml`:

```yaml
services:
  memfs-sidecar:
    build:
      context: ./local_letta_memfs_magic/docker
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      - MEMFS_PORT=8285
      - MEMFS_BASE_PATH=/data/memfs/repository
      - MEMFS_DEFAULT_ORG=default-org
    volumes:
      - type: bind
        source: ${HOME}/.letta/.persist/memfs
        target: /data/memfs/repository
    deploy:
      resources:
        limits:
          memory: 256M
    networks:
      - letta-network
```

---

## Step 3 - Configure the Letta Service

Add these settings to your existing Letta service:

```yaml
  letta:
    depends_on:
      memfs-sidecar:
        condition: service_healthy
    environment:
      - LETTA_MEMFS_SERVICE_URL=http://memfs-sidecar:8285
    volumes:
      # Override memfs_client_base.py to fix redis_client kwarg mismatch
      - ./overrides/letta/services/memory_repo/memfs_client_base.py:/app/letta/services/memory_repo/memfs_client_base.py:ro
      # Mount the same memfs storage as the sidecar
      - type: bind
        source: ${HOME}/.letta/.persist/memfs
        target: /root/.letta/memfs/repository
```

### The memfs_client_base.py Override

The stock `memfs_client_base.py` passes `redis_client=None` to
`GitOperations.__init__()`, which doesn't accept that keyword argument.
This is a mismatch between the OSS base client and the cloud client.

The override file fixes this single line:

```python
# Before (broken):
self.git = GitOperations(storage=self.storage, redis_client=None)

# After (fixed):
self.git = GitOperations(storage=self.storage)
```

A complete working override is included in the `overrides/` directory
of a typical deployment. The override also includes the full
`MemfsClient` class with proper `create_repo_async`, `get_blocks_async`,
`update_block_async`, etc. methods.

---

## Step 4 - Enable Git Memory on an Agent

Use the Letta API or Letta Code to enable git memory:

```bash
# Via Letta Code (requires LETTA_MEMFS_LOCAL=1 in the Letta Code shell):
letta --memfs

# Via API (tag the agent server-side):
curl -X POST "http://localhost:8283/v1/agents/{agent_id}/tags/" \
  -H "Authorization: Bearer {password}" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["git-memory-enabled"]}'
```

The `GitEnabledBlockManager` automatically intercepts block operations
when it detects the `git-memory-enabled` tag on an agent.

---

## Step 5 - Verify It Works

```bash
# Check the sidecar is healthy
curl http://localhost:8285/health
# Expected: {"status": "ok"}

# Update a block and check for git commits
curl -X PATCH "http://localhost:8283/v1/blocks/{block_id}/" \
  -H "Authorization: Bearer {password}" \
  -H "Content-Type: application/json" \
  -d '{"value": "test commit"}'

# Inspect the git repo directly
docker exec letta-letta-1 bash -c \
  'cd /root/.letta/memfs/repository/{org_id}/{agent_id}/repo.git && git log --oneline'
```

Check the Letta container logs for confirmation:
```
[GIT_PERF] update_block_async TOTAL 317.53ms (git-enabled path)
```

If you see `(postgres-only path)` instead, the agent doesn't have the
`git-memory-enabled` tag, or the shared-block bug is affecting you
(see below).

---

## Troubleshooting

### "TypeError: GitOperations.__init__() got an unexpected keyword argument 'redis_client'"
The `memfs_client_base.py` override is not mounted. Check your volume
mount in `compose.yaml`.

### Block updates show "(postgres-only path)" in logs
1. Verify the agent has the `git-memory-enabled` tag
2. If the block is shared across multiple agents, you may be hitting the
   shared-block bug. See `docs/SHARED-BLOCK-BUG.md` for the fix.

### Sidecar returns 400 "Invalid path"
The Letta server's `/v1/git/` proxy is sending requests to the sidecar
but the URL pattern doesn't match. Verify `LETTA_MEMFS_SERVICE_URL` is
set correctly in the Letta container environment.

### Repo exists but has no commits
The initial commit happens when `enable_git_memory_for_agent` is called
(via `letta --memfs` or the API). Just tagging the agent is not enough;
the `GitEnabledBlockManager.enable_git_memory_for_agent` method must run
to create the repo and initial commit.

---

## Volume Permissions

The memfs-sidecar container runs as root by default. The Letta container
also writes as root. If you're running on a system with restrictive
permissions, ensure both containers can read/write the shared volume:

```bash
chmod -R 755 ~/.letta/.persist/memfs
```

---

## Backup and Restore

The memfs data is just bare git repos on disk. Back up by copying the
directory:

```bash
tar czf memfs-backup-$(date +%F).tar.gz ~/.letta/.persist/memfs/
```

Restore by extracting to the same path and restarting the containers.

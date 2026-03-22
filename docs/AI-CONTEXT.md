# AI Context — Letta Local MemFS Setup

This document is for an AI assistant helping debug or extend this setup.
Read this before touching anything.

---

## What Was Built and Why

Letta's MemFS (git-backed memory) is officially cloud-only. The OSS server
has all the plumbing but the git HTTP transport endpoint (`/v1/git/`) is a
proxy that requires `LETTA_MEMFS_SERVICE_URL` to be set — and the cloud
git backend isn't shipped in the OSS image.

This setup bridges that gap with a local Python HTTP server that speaks
the git smart-HTTP protocol, backed by bare repos on the local filesystem.

---

## Architecture

```
Letta Code (letta.js, Node)
    |
    | git clone/push/pull
    v
Letta Server :8283  (uv run, C:\c\apps\clients\letta)
    |
    | /v1/git/{agent_id}/state.git/* → proxied via httpx
    v
git-memfs-server.py :8285  (Python stdlib HTTPServer)
    |
    | sets GIT_PROJECT_ROOT + PATH_INFO, calls subprocess
    v
git http-backend  (Git for Windows, C:\Program Files\Git)
    |
    | reads/writes bare repo
    v
~/.letta/memfs/repository/{org_id}/{agent_id}/repo.git/
```

Redis :6379 is also required — GitOperations.commit() acquires a Redis
lock per agent before writing. Redis runs from:
  C:\c\apps\pkm\redis-windows\redis-server.exe --protected-mode no

---

## Key Files and Their Roles

**git-memfs-server.py** (`C:\c\apps\servers\`)
  - Python `http.server.HTTPServer` on 127.0.0.1:8285
  - Parses `/git/{agent_id}/state.git/{op}` URLs
  - Reads `X-Organization-Id` header to locate the right bare repo
  - Handles both `Content-Length` and chunked `Transfer-Encoding`
    (Letta's httpx proxy strips Content-Length and re-sends chunked)
  - Sets `GIT_PROJECT_ROOT` + `PATH_INFO=/repo.git/{op}` env vars
  - Calls `git http-backend` as a subprocess
  - Parses CGI-style response (headers + \r\n\r\n + body)
  - Auto-creates bare repos on first clone with `http.receivepack=true`

**letta/services/memory_repo/memfs_client_base.py**
  - Local MemfsClient used when `memfs_client.py` (cloud) isn't importable
  - Patched: removed `redis_client=None` kwarg from GitOperations() call
    (GitOperations.__init__ only accepts `storage`)
  - Stores repos via LocalStorageBackend at `~/.letta/memfs/`

**letta/services/memory_repo/git_operations.py**
  - Does actual git work via subprocess git CLI
  - commit() acquires Redis lock via get_redis_client()
  - Uses temp dirs: download repo → modify → upload delta back to storage

**~/.letta/conf.yaml**
  - MUST use nested format:
    ```yaml
    letta:
      memfs_service_url: "http://localhost:8285"
    ```
  - Bare `LETTA_MEMFS_SERVICE_URL: "..."` does NOT work — config_file.py
    requires the `letta:` top-level key and flattens to env vars

**~/.bashrc** (Git Bash)
  - Contains: `export LETTA_MEMFS_LOCAL=1`
  - This makes letta.js isLettaCloud() return true, bypassing the
    cloud-only guard in applyMemfsFlags()

**~/.bash_profile** (Git Bash)
  - Sources ~/.bashrc — required because Git Bash opens as a login shell
    which reads .bash_profile, not .bashrc directly

---

## Bugs Fixed During Setup

1. **conf.yaml format** — bare env var keys don't work; must nest under `letta:`
2. **redis_client kwarg** — memfs_client_base.py passed `redis_client=None`
   to GitOperations which doesn't accept it; removed the kwarg
3. **GIT_DIR vs PATH_TRANSLATED vs GIT_PROJECT_ROOT** — git http-backend
   requires `GIT_PROJECT_ROOT` (parent of repo.git) + `PATH_INFO=/repo.git/...`
   NOT `GIT_DIR` or `PATH_TRANSLATED`
4. **Windows backslashes** — GIT_PROJECT_ROOT must use forward slashes
5. **Chunked transfer encoding** — Letta's httpx proxy strips Content-Length
   and re-encodes as chunked; server must decode chunks manually
6. **LETTA_MEMFS_LOCAL env var** — was in .bashrc but Git Bash (login shell)
   never read .bashrc; fixed by creating .bash_profile that sources .bashrc
7. **CWD EPERM** — letta.js tries to mkdir `.letta` in CWD; running from
   C:\Program Files\Git\ (Git Bash root `/`) caused permission error;
   fixed by auto-cd to ~ in .bash_profile

---

## How to Diagnose Problems

**Check if memfs service URL is being loaded:**
```powershell
cd C:\c\apps\clients\letta
uv run python C:\c\apps\servers\check_settings.py
# Should print: memfs_service_url: http://localhost:8285
# If None: conf.yaml isn't being read or has wrong format
```

**Check if env var is live in Git Bash:**
```bash
echo $LETTA_MEMFS_LOCAL
# Should print: 1
# If blank: open a fresh Git Bash window
```

**Test the full proxy chain:**
```powershell
python C:\c\apps\servers\test_proxy.py
# Should print: STATUS: 200, Content-Type: application/x-git-upload-pack-advertisement
```

**Test git-backend directly:**
```powershell
python C:\c\apps\servers\debug_memfs.py
```

**Watch live git traffic:**
The memfs server prints every request. Check that window during `letta --memfs`.

---

## What MemFS Does to an Agent

When `--memfs` is enabled on an agent:
- The `git-memory-enabled` tag is added to the agent in Postgres
- Standard memory tools (memory_insert, memory_replace, etc.) are detached
- human/persona blocks are detached
- Memory moves to files in the git repo:
  - `system/*.md` files → pinned into system prompt (replaces core blocks)
  - Other dirs → progressive memory, loaded on demand with Read tool
- GitEnabledBlockManager intercepts all block writes → git-commits them
- The agent can use Bash + git to manage its own memory history

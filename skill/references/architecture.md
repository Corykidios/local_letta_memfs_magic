# Architecture & Technical Internals

## How the Pieces Fit Together

### Letta's existing OSS code (nothing invented here)

`letta/services/memory_repo/__init__.py` tries to import the cloud client:
```python
try:
    from letta.services.memory_repo.memfs_client import MemfsClient
except ImportError:
    from letta.services.memory_repo.memfs_client_base import MemfsClient
```
OSS installs always use `memfs_client_base.py` — the local filesystem client.

`LocalStorageBackend` stores git object data under `~/.letta/memfs/` as flat
files. `GitOperations` runs actual git commands via subprocess (no dulwich).

`server.py._init_memory_repo_manager()` only activates this if
`LETTA_MEMFS_SERVICE_URL` is set:
```python
if not settings.memfs_service_url:
    return None   # memfs disabled
return MemfsClient(base_url=settings.memfs_service_url)
```

`git_http.py` proxies `/v1/git/*` to `settings.memfs_service_url` via httpx.
Without the URL set, it returns 501. With it set, it forwards to our sidecar.

### The sidecar (git-memfs-server.py)

Receives proxied requests from Letta's httpx proxy. Maps:
```
/git/{agent_id}/state.git/info/refs    →  GIT_PROJECT_ROOT + PATH_INFO=/repo.git/info/refs
/git/{agent_id}/state.git/git-upload-pack  →  PATH_INFO=/repo.git/git-upload-pack
/git/{agent_id}/state.git/git-receive-pack →  PATH_INFO=/repo.git/git-receive-pack
```

Calls `git http-backend` as a CGI subprocess. Parses CGI-style response
(headers block + `\r\n\r\n` + body). Returns to Letta's proxy.

Key env vars for `git http-backend`:
- `GIT_PROJECT_ROOT` — parent dir of `repo.git/` (forward slashes, even on Windows)
- `PATH_INFO` — must include `/repo.git/` prefix
- `GIT_HTTP_EXPORT_ALL` — allows cloning without explicit export marker

### conf.yaml loading

`letta/config_file.py` reads `~/.letta/conf.yaml` and flattens to env vars.
The file MUST be nested under a known top-level key:
```yaml
letta:                          # ← required
  memfs_service_url: "http://localhost:8285"
```
This becomes `LETTA_MEMFS_SERVICE_URL=http://localhost:8285` in the env.
Bare top-level keys are silently ignored.

### LETTA_MEMFS_LOCAL client bypass

`letta.js` `isLettaCloud()` function (appears twice, minified):
```javascript
async function isLettaCloud() {
  const serverUrl = getServerUrl();
  return serverUrl.includes("api.letta.com") || process.env.LETTA_MEMFS_LOCAL === "1";
}
```
Setting `LETTA_MEMFS_LOCAL=1` makes this return `true`, bypassing the throw
in `applyMemfsFlags()`. The check is `=== "1"` not `=== "true"` — must be `1`.

---

## Bugs Found and Fixed During Original Setup

| # | Bug | Root Cause | Fix |
|---|-----|------------|-----|
| 1 | `conf.yaml` not loaded | Bare key format instead of nested `letta:` | Nest under `letta:` key |
| 2 | `TypeError: unexpected keyword argument 'redis_client'` | `memfs_client_base.py` passes kwarg that `GitOperations.__init__` doesn't accept | Remove `redis_client=None` from call |
| 3 | git http-backend: "No GIT_PROJECT_ROOT or PATH_TRANSLATED" | Used `GIT_DIR` instead of `GIT_PROJECT_ROOT` | Switch to `GIT_PROJECT_ROOT` |
| 4 | git http-backend: "Request not supported" | Windows backslashes in `GIT_PROJECT_ROOT` | `.replace("\\", "/")` |
| 5 | "expected flush after ref listing" | Letta's httpx proxy strips `Content-Length`, re-encodes as chunked; server read 0 bytes | Added chunked transfer decoding |
| 6 | "only available on Letta Cloud" despite `.bashrc` export | Git Bash is a login shell; reads `.bash_profile` not `.bashrc` | Create `.bash_profile` that sources `.bashrc` |
| 7 | "EPERM mkdir ...Git\.letta" | `letta.js` tries to mkdir `.letta` in CWD; Git Bash `/` = `C:\Program Files\Git\` | `cd ~` in `.bash_profile` |

---

## Extending the Sidecar

### Multi-machine portability
The bare repos live at `~/.letta/memfs/repository/` on one machine.
Options for multi-machine:
- Copy the entire `~/.letta/memfs/` directory to the new machine
- Point `MEMFS_BASE` in the sidecar at a network share or synced folder
- Replace `LocalStorageBackend` with an S3/GCS backend (the interface is
  already defined in `storage/base.py`)

### Running as a Windows service
Use NSSM (Non-Sucking Service Manager) to wrap the Python sidecar:
```
nssm install letta-memfs python C:\path\to\git-memfs-server.py
nssm start letta-memfs
```

### Changing the port
Edit `PORT = 8285` in `git-memfs-server.py` and update `conf.yaml` to match.

### Docker Letta (untested)
The sidecar needs to be accessible from inside the container.
Options:
- Run sidecar on the host, point container's `LETTA_MEMFS_SERVICE_URL`
  at `host.docker.internal:8285`
- Add the sidecar to the docker-compose as a separate service

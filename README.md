# Local MemFS for Self-Hosted Letta — Complete Working Guide

**Status: Working as of Letta v0.16.6 / Letta Code v0.19.6 (March 2026)**

This guide explains how to run Letta's git-backed memory (MemFS / context
repositories) on a fully self-hosted OSS Letta server — no Letta Cloud
account required.

---

## Background

MemFS is officially cloud-only. The OSS server has all the internal plumbing
(`GitEnabledBlockManager`, `LocalStorageBackend`, `GitOperations`) but the
git HTTP transport endpoint (`/v1/git/`) is a proxy that needs a real git
server behind it — and that server isn't bundled in the OSS image.

The fix is a ~100-line Python sidecar that acts as that git server, backed
by bare repos on your local filesystem. No dulwich, no Gitea, no cloud
account. Just Python stdlib + the `git http-backend` CGI handler that ships
with Git for Windows (or any standard Git install).

---

## What You Need

- Letta OSS server (pip or uv install, Docker *not* tested)
- Letta Code v0.16.0+ (the `LETTA_MEMFS_LOCAL` env var was added here)
- Git installed (Git for Windows on Windows; any git on Linux/Mac)
- Python 3.x (for the sidecar server)
- Redis (Letta's git commit path acquires a Redis lock per agent)

---

## Step 1 — The Sidecar Server

Save this as `git-memfs-server.py` anywhere convenient:


```python
#!/usr/bin/env python3
"""
git-memfs-server.py — Local git HTTP smart protocol server for Letta MemFS.
Serves bare git repos via git http-backend so Letta Code can clone/push/pull
against your own machine instead of Letta Cloud.
"""
import os, subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = 8285
MEMFS_BASE = Path.home() / ".letta" / "memfs" / "repository"
DEFAULT_ORG = "default-org"

def find_or_create_repo(agent_id, org_id):
    repo = MEMFS_BASE / org_id / agent_id / "repo.git"
    if not repo.exists():
        if MEMFS_BASE.exists():
            for org_dir in MEMFS_BASE.iterdir():
                candidate = org_dir / agent_id / "repo.git"
                if candidate.exists():
                    return candidate
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "http.receivepack", "true"],
                       check=True, capture_output=True)
        print(f"[git-memfs] Created bare repo at {repo}", flush=True)
    return repo

class GitHTTPHandler(BaseHTTPRequestHandler):
    def _read_body(self):
        te = self.headers.get("Transfer-Encoding", "")
        if "chunked" in te.lower():
            body = b""
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line: break
                try: chunk_size = int(size_line, 16)
                except ValueError: break
                if chunk_size == 0:
                    self.rfile.readline()
                    break
                body += self.rfile.read(chunk_size)
                self.rfile.readline()
            return body
        else:
            n = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(n) if n > 0 else b""

    def _parse_path(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "git": return None, None, None
        agent_id = parts[1]
        git_op = "/" + "/".join(parts[3:]) if len(parts) > 3 else "/"
        return agent_id, git_op, parsed.query or ""

    def _run_backend(self):
        agent_id, git_op, query = self._parse_path()
        if agent_id is None:
            self.send_error(400, "Expected /git/{agent_id}/state.git/...")
            return
        org_id = self.headers.get("X-Organization-Id", DEFAULT_ORG)
        repo_path = find_or_create_repo(agent_id, org_id)
        body = self._read_body()
        project_root = str(repo_path.parent).replace("\\", "/")
        env = {**os.environ,
            "GIT_HTTP_EXPORT_ALL": "1",
            "GIT_PROJECT_ROOT": project_root,
            "PATH_INFO": "/repo.git" + git_op,
            "QUERY_STRING": query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            "REMOTE_ADDR": "127.0.0.1", "REMOTE_USER": "",
            "SERVER_NAME": "localhost", "SERVER_PORT": str(PORT),
            "SERVER_PROTOCOL": "HTTP/1.1"}
        result = subprocess.run(["git", "http-backend"], input=body,
                                capture_output=True, env=env)
        if result.returncode != 0:
            print(f"[git-memfs] error: {result.stderr.decode(errors='replace')}", flush=True)
            self.send_error(500); return
        raw = result.stdout
        for sep in [b"\r\n\r\n", b"\n\n"]:
            pos = raw.find(sep)
            if pos != -1: break
        else:
            self.send_error(502); return
        header_block = raw[:pos].decode(errors="replace")
        body_out = raw[pos + len(sep):]
        status = 200
        headers = []
        for line in header_block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":"); k, v = k.strip(), v.strip()
                if k.lower() == "status":
                    try: status = int(v.split()[0])
                    except ValueError: pass
                else: headers.append((k, v))
        self.send_response(status)
        for k, v in headers: self.send_header(k, v)
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def do_GET(self): self._run_backend()
    def do_POST(self): self._run_backend()
    def log_message(self, fmt, *args):
        print(f"[git-memfs] {self.address_string()} — {fmt % args}", flush=True)

if __name__ == "__main__":
    MEMFS_BASE.mkdir(parents=True, exist_ok=True)
    print(f"[git-memfs] Starting on http://127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), GitHTTPHandler).serve_forever()
```

---

## Step 2 — Patch One Line in the Letta Server

In your Letta install, open:
`letta/services/memory_repo/memfs_client_base.py`

Find line ~54:
```python
self.git = GitOperations(storage=self.storage, redis_client=None)
```
Change it to:
```python
self.git = GitOperations(storage=self.storage)
```

That's the only source code change needed. `GitOperations.__init__` doesn't
accept `redis_client` — this is a mismatch between the base and cloud clients.

---

## Step 3 — Configure Letta to Use the Sidecar

Create or edit `~/.letta/conf.yaml`:

```yaml
letta:
  memfs_service_url: "http://localhost:8285"
```

**Important:** the nested `letta:` key is required. A bare
`LETTA_MEMFS_SERVICE_URL: "..."` at the top level won't work — Letta's
config loader only flattens keys under known top-level sections.

Alternatively, set the environment variable directly before starting the server:
```bash
export LETTA_MEMFS_SERVICE_URL=http://localhost:8285
```

---

## Step 4 — Set the Client Env Var

Letta Code has a client-side cloud check. Bypass it with:

```bash
export LETTA_MEMFS_LOCAL=1
```

Add this to your `~/.bashrc` (and make sure `~/.bash_profile` sources it,
since most terminals open as login shells and skip `.bashrc` otherwise):

```bash
# ~/.bash_profile
if [ -f ~/.bashrc ]; then source ~/.bashrc; fi
```

On Windows with Git Bash, running `letta` from `/` (the Git install root)
causes a permissions error. Either `cd ~` first, or add `cd ~` to your
`.bash_profile`.

---

## Step 5 — Start Everything

Start in this order:

```bash
# Terminal 1 — Redis
redis-server --protected-mode no

# Terminal 2 — MemFS sidecar
python git-memfs-server.py

# Terminal 3 — Letta server
cd /path/to/your/letta
uv run letta server   # or: pip install letta && letta server

# Terminal 4 (Git Bash / bash) — Letta Code
letta --memfs
```

When you run `letta --memfs`, watch Terminal 2. You should see:
```
[git-memfs] Created bare repo at ~/.letta/memfs/repository/{org}/{agent}/repo.git
[git-memfs] 127.0.0.1 — "GET /git/{agent}/state.git/info/refs?service=git-upload-pack HTTP/1.1" 200
[git-memfs] 127.0.0.1 — "POST /git/{agent}/state.git/git-upload-pack HTTP/1.1" 200
```

The `--memfs` flag is only needed once per agent. After the first enable,
the `git-memory-enabled` tag is stored on the agent server-side and Letta
Code enables memfs automatically on subsequent launches.

---

## How It Works (Brief)

The Letta server's `/v1/git/` router proxies git smart-HTTP requests to
`LETTA_MEMFS_SERVICE_URL`. Our sidecar receives those requests, maps the
agent ID to a local bare repo under `~/.letta/memfs/repository/`, and
delegates to `git http-backend` (the CGI handler bundled with every Git
install) which handles the actual git protocol.

The `LocalStorageBackend` and `MemfsClient` classes already exist in the
OSS codebase — they store git object data on disk without any cloud
dependency. The sidecar just provides the HTTP transport layer that was
previously missing.

---

## Verified Working On

- Windows 11, Git for Windows 2.52, Python 3.14, Node.js 24
- Letta v0.16.6 (uv install), Letta Code v0.19.6
- PostgreSQL + Redis backend

Linux/Mac should work identically — the only Windows-specific detail is
forward-slashing `GIT_PROJECT_ROOT` (forward slashes required even on
Windows for `git http-backend`).

---

## Known Limitations

- The sidecar is HTTP only (no TLS). Fine for localhost; don't expose it.
- Multi-machine portability: the bare repos live at `~/.letta/memfs/` on
  one machine. Moving machines means copying that directory or pointing
  `MEMFS_BASE` at a network share.
- Docker Letta: not tested. The conf.yaml and sidecar approach should work
  the same way, but the sidecar needs to be accessible from inside the container.

---

*Credit: worked out with Claude Sonnet 4.6 via the Claude.ai desktop app,
March 2026. First confirmed working self-hosted MemFS setup.*
